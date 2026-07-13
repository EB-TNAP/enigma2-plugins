from __future__ import print_function
from . import _
from enigma import eConsoleAppContainer, eDVBFrontendParametersSatellite, eDVBResourceManager, eDVBSatelliteEquipmentControl
from Components.config import config
from Components.NimManager import nimmanager
from Components.TuneTest import Tuner
from Screens.MessageBox import MessageBox
from time import time
from . import bsconfig
from .bsconfig import (BOX_MODEL, BOX_NAME, BLINDSCAN_STEP_SETTLE_MS,
                       _supportNimType, _blindscans2Nims, getMisPlsValue, root2gold)
from .bscommands import build_scan_command, _HardwareNotSupported, _ToolNotFound
from .bsscreens import BlindscanState
from .bsverify import verifyDebugException


class BlindscanEngineMixin(object):

	def ScanNimsocket(self, filepath='/proc/bus/nim_sockets'):
		_nimSocket = {}
		try:
			fp = open(filepath)
		except:
			return _nimSocket
		sNo, sName, sI2C = -1, "", -1
		for line in fp:
			line = line.strip()
			if line.startswith('NIM Socket'):
				sNo, sName, sI2C = -1, '', -1
				try:
					sNo = line.split()[2][:-1]
				except:
					sNo = -1
			elif line.startswith('I2C_Device:'):
				try:
					sI2C = line.split()[1]
				except:
					sI2C = -1
			elif line.startswith('Name:'):
				splitLines = line.split()
				try:
					if splitLines[1].startswith('BCM'):
						sName = splitLines[1]
					else:
						sName = splitLines[3][4:-1]
				except:
					sName = ""
			if sNo != -1 and sName != "":
				if sName.startswith('BCM'):
					sI2C = sNo
				if sI2C != -1:
					_nimSocket[sNo] = [sName, sI2C]
				else:
					_nimSocket[sNo] = [sName]
		fp.close()
		print("[Blindscan][ScanNimsocket] parsed nimsocket:", _nimSocket)
		return _nimSocket

	def makeNimSocket(self, nimname=""):
		is_exist_i2c = False
		self.i2c_mapping_table = {0: 2, 1: 3, 2: 1, 3: 0}
		if self.nimSockets is not None:
			for XX in self.nimSockets.keys():
				nimsocket = self.nimSockets[XX]
				if len(nimsocket) > 1:
					try:
						self.i2c_mapping_table[int(XX)] = int(nimsocket[1])
					except:
						continue
					is_exist_i2c = True
		print("[Blindscan][makeNimSocket] i2c_mapping_table:", self.i2c_mapping_table, ", is_exist_i2c:", is_exist_i2c)
		if is_exist_i2c:
			return

		if nimname == "AVL6222":
			if BOX_NAME == "uno":
				self.i2c_mapping_table = {0: 3, 1: 3, 2: 1, 3: 0}
			elif BOX_NAME == "duo2":
				nimdata = self.nimSockets['0']
				try:
					if nimdata[0] == "AVL6222":
						self.i2c_mapping_table = {0: 2, 1: 2, 2: 4, 3: 4}
					else:
						self.i2c_mapping_table = {0: 2, 1: 4, 2: 4, 3: 0}
				except:
					self.i2c_mapping_table = {0: 2, 1: 4, 2: 4, 3: 0}
			else:
				self.i2c_mapping_table = {0: 2, 1: 4, 2: 0, 3: 0}
		else:
			self.i2c_mapping_table = {0: 2, 1: 3, 2: 1, 3: 0}

	def getNimSocket(self, slot_number):
		return self.i2c_mapping_table.get(slot_number, -1)

	def callbackNone(self, *retval):
		None

	def openFrontend(self):
		res_mgr = eDVBResourceManager.getInstance()
		if res_mgr:
			self.raw_channel = res_mgr.allocateRawChannel(self.feid)
			if self.raw_channel:
				self.frontend = self.raw_channel.getFrontend()
				if self.frontend:
					print("[Blindscan][openFrontend] allocated raw channel on feid %d, frontend=%r" % (self.feid, self.frontend))
					return True
				else:
					print("[Blindscan][openFrontend] getFrontend failed")
			else:
				print("[Blindscan][openFrontend] getRawChannel failed")
		else:
			print("[Blindscan][openFrontend] getResourceManager instance failed")
		return False

	def prepareFrontend(self):
		self.releaseFrontend()
		if not self.openFrontend():
			oldref = self.session.nav.getCurrentlyPlayingServiceReference()
			stop_current_service = True
			if oldref and self.getCurrentTuner is not None:
				if self.feid != self.getCurrentTuner:
					stop_current_service = False
			if stop_current_service:
				self.session.nav.stopService()
				self.getCurrentTuner = None
			if not self.openFrontend():
				if self.session.pipshown:
					if hasattr(self.session, 'infobar'):
						try:
							slist = self.session.infobar.servicelist
							if slist and slist.dopipzap:
								slist.togglePipzap()
						except:
							pass
					self.session.pipshown = False
					if hasattr(self.session, 'pip'):
						del self.session.pip
					self.openFrontend()
		print('[Blindscan] self.frontend:', self.frontend)
		if self.frontend is None:
			text = _("Sorry, this tuner is in use.")
			if self.session.nav.getRecordings():
				text += "\n"
				text += _("Maybe the reason that recording is currently running.")
			# FIXED: Use openWithCallback instead of open to prevent modal dialog crash
			# when called from non-modal context (e.g., during initialization)
			self.session.openWithCallback(self.prepareFrontendErrorCallback, MessageBox, text, MessageBox.TYPE_ERROR, timeout=5)
			return False
		self.tuner = Tuner(self.frontend)
		return True

	def prepareFrontendErrorCallback(self, answer=None):
		"""Callback for prepareFrontend error message - does nothing, just prevents crash"""
		# This callback is needed because openWithCallback requires a callback function
		# We don't need to do anything here as prepareFrontend already returns False
		pass

	def doRun(self, tmp_list, tmp_pol, tmp_band):
		cur_orb_pos = self.getOrbPos()
		if cur_orb_pos == 2571:
			self.session.open(MessageBox, _("Blindscan is not supported for this satellite "))
			print('----781 blindscan orbit = 2571----Scan Aborted!')
			return
		print("[Blindscan][doRun] started")

		def GetCommand(nimIdx):
			_nimSocket = self.nimSockets
			try:
				sName = _nimSocket[str(nimIdx)][0]
				sType = _supportNimType[sName]
				return "vuplus_%(TYPE)sblindscan" % {'TYPE': sType}, sName
			except:
				pass
			return "vuplus_blindscan", ""
		if BOX_MODEL == "vuplus":
			self.binName, nimName = GetCommand(self.scan_nims.value)

			self.makeNimSocket(nimName)
			if self.binName is None:
				self.session.open(MessageBox, _("Blindscan is not supported in ") + nimName + _(" tuner."), MessageBox.TYPE_ERROR)
				print("[Blindscan][doRun] " + nimName + " does not support blindscan.")
				return

		self.full_data = ""
		self.total_list = []
		for x in tmp_list:
			for y in tmp_pol:
				for z in tmp_band:
					self.total_list.append([x, y, z])
					print("[Blindscan][doRun] add scan item: ", x, ", ", y, ", ", z)

		self.max_count = len(self.total_list)
		self.running_count = 0
		self.scan_aborted = False
		self.start_time = time()

		tuner = nimmanager.nim_slots[int(self.scan_nims.value)].friendly_full_description
		init_progress = _("Preparing blind scan...")
		init_action = _("Looking for available transponders.\n \n" + tuner + "\n \n")
		self.progress_base = init_progress
		# tuner_slot pins the panel's live signal monitor/graph to the tuner
		# actually being blindscanned (same source of truth as self.feid,
		# which prepareScanData() hasn't set yet at this point).
		self.panel = self.session.openWithCallback(self.panelClosed, BlindscanState, init_progress, init_action, [], tuner_slot=int(self.scan_nims.value))
		self.blindscan_session = self.panel
		# The scan panel fully covers this config screen for the whole multi-step scan.
		# Hide it so the compositor stops blending a screen nobody can see. panelClosed()
		# shows it again when control returns. Purely a resource optimization - no visible
		# change, since the panel's opaque background already covers this screen's area.
		self.hide()
		# Live elapsed-time stopwatch on the progress line, updated once per second.
		self.elapsedTimer.start(1000)

		self.runStep()

	def prepareScanData(self, orb, pol, band, is_scan):
		print("[Blindscan][prepareScanData] started")
		self.is_runable = False
		self.adjust_freq = True
		self.orb_position = orb[0]
		self.sat_name = orb[1]
		self.feid = int(self.scan_nims.value)
		tab_hilow = {"high": 1, "low": 0}
		tab_pol = {
			"horizontal": eDVBFrontendParametersSatellite.Polarisation_Horizontal,
			"vertical": eDVBFrontendParametersSatellite.Polarisation_Vertical,
			"circular left": eDVBFrontendParametersSatellite.Polarisation_CircularLeft,
			"circular right": eDVBFrontendParametersSatellite.Polarisation_CircularRight
		}
		uni_lnb_cutoff = self.uni_lnb_cutoff

		if not self.prepareFrontend():
			print("[Blindscan][prepareScanData] self.prepareFrontend() failed (in prepareScanData)")
			return False

		random_ku_band_low_tunable_freq = 11015 # used to activate the tuner
		random_c_band_tunable_freq = 3400 # used to activate the tuner
		random_c_band_5750_tunable_freq = 3600

		if self.is_c_band_scan:
			tuning_frequency = random_c_band_tunable_freq
		elif self.is_c_band_5750_scan:
			tuning_frequency = random_c_band_5750_tunable_freq
		elif self.is_c_band_bandstack_scan:
			tuning_frequency = random_c_band_5750_tunable_freq
######################

		elif self.user_defined_lnb_scan:
			tuning_frequency = random_ku_band_low_tunable_freq + (self.user_defined_lnb_lo_freq - self.universal_lo_freq["low"])
		else:
			if tab_hilow[band]: # high band
				tuning_frequency = random_ku_band_low_tunable_freq + (self.universal_lo_freq["high"] - self.universal_lo_freq["low"]) #used to be 12515
			else: # low band
				tuning_frequency = random_ku_band_low_tunable_freq

		print("[Blindscan][prepareScanData] activation tune: freq=%d pol=%s orb=%s frontend=%r rotor_moving=%s" % (tuning_frequency, tab_pol[pol], str(orb[0]), self.frontend, eDVBSatelliteEquipmentControl.getInstance().isRotorMoving()))
		self.tuner.tune(
			(tuning_frequency,
			0, # symbolrate
			tab_pol[pol],
			eDVBFrontendParametersSatellite.FEC_Auto,
			eDVBFrontendParametersSatellite.Inversion_Off,
			orb[0],
			eDVBFrontendParametersSatellite.System_DVB_S,
			eDVBFrontendParametersSatellite.Modulation_Auto,
			eDVBFrontendParametersSatellite.RollOff_alpha_0_35,
			eDVBFrontendParametersSatellite.Pilot_Off,
			eDVBFrontendParametersSatellite.No_Stream_Id_Filter,
			eDVBFrontendParametersSatellite.PLS_Gold,
			eDVBFrontendParametersSatellite.PLS_Default_Gold_Code,
			eDVBFrontendParametersSatellite.No_T2MI_PLP_Id,
			eDVBFrontendParametersSatellite.T2MI_Default_Pid)
		)

		nim = nimmanager.nim_slots[self.feid]
		tunername = nim.description
		if tunername not in _blindscans2Nims and self.getNimSocket(self.feid) < 0:
			print("[Blindscan][prepareScanData] can't find i2c number!!")
			return

		if self.is_c_band_scan:
			temp_start_int_freq = self.c_band_lo_freq - self.blindscan_stop_frequency
			temp_end_int_freq = self.c_band_lo_freq - self.blindscan_start_frequency
			status_box_start_freq = self.c_band_lo_freq - temp_end_int_freq
			status_box_end_freq = self.c_band_lo_freq - temp_start_int_freq
		elif self.is_c_band_5750_scan:
			temp_start_int_freq = self.c_band_5750_lo_freq - self.blindscan_stop_frequency
			temp_end_int_freq = self.c_band_5750_lo_freq - self.blindscan_start_frequency
			status_box_start_freq = self.c_band_5750_lo_freq - temp_end_int_freq
			status_box_end_freq = self.c_band_5750_lo_freq - temp_start_int_freq
		elif self.is_c_band_bandstack_scan:
			# For C-band bandstacked LNB, polarization determines which LO to use
			if pol == "vertical":
				# Use c_band_lo_freq (5150 MHz) for vertical polarization
				temp_start_int_freq = self.c_band_lo_freq - self.blindscan_stop_frequency
				temp_end_int_freq = self.c_band_lo_freq - self.blindscan_start_frequency
				status_box_start_freq = self.c_band_lo_freq - temp_end_int_freq
				status_box_end_freq = self.c_band_lo_freq - temp_start_int_freq
			else:
				# Use c_band_5750_lo_freq (5750 MHz) for horizontal polarization
				temp_start_int_freq = self.c_band_5750_lo_freq - self.blindscan_stop_frequency
				temp_end_int_freq = self.c_band_5750_lo_freq - self.blindscan_start_frequency
				status_box_start_freq = self.c_band_5750_lo_freq - temp_end_int_freq
				status_box_end_freq = self.c_band_5750_lo_freq - temp_start_int_freq

##################

		elif self.user_defined_lnb_scan:
			temp_start_int_freq = self.blindscan_start_frequency - self.universal_lo_freq["low"]
			temp_end_int_freq = self.blindscan_stop_frequency - self.universal_lo_freq["low"]
			if config.blindscan.user_defined_lnb_inversion.value:
				status_box_start_freq = self.user_defined_lnb_lo_freq - temp_end_int_freq
				status_box_end_freq = self.user_defined_lnb_lo_freq - temp_start_int_freq
			else:
				status_box_start_freq = self.blindscan_start_frequency + (self.user_defined_lnb_lo_freq - self.universal_lo_freq["low"])
				status_box_end_freq = self.blindscan_stop_frequency + (self.user_defined_lnb_lo_freq - self.universal_lo_freq["low"])
		else:
			if tab_hilow[band]:
				if self.blindscan_start_frequency < uni_lnb_cutoff:
					temp_start_int_freq = uni_lnb_cutoff - self.universal_lo_freq[band]
				else:
					temp_start_int_freq = self.blindscan_start_frequency - self.universal_lo_freq[band]
				temp_end_int_freq = self.blindscan_stop_frequency - self.universal_lo_freq[band]
			else:
				if self.blindscan_stop_frequency > uni_lnb_cutoff:
					temp_end_int_freq = uni_lnb_cutoff - self.universal_lo_freq[band]
				else:
					temp_end_int_freq = self.blindscan_stop_frequency - self.universal_lo_freq[band]
				temp_start_int_freq = self.blindscan_start_frequency - self.universal_lo_freq[band]
			status_box_start_freq = temp_start_int_freq + self.universal_lo_freq[band]
			status_box_end_freq = temp_end_int_freq + self.universal_lo_freq[band]
		if self.user_defined_lnb_scan:
			self.start_freq = status_box_start_freq  # Start Freq. key for ServiceScan
			self.end_freq = status_box_end_freq  # End Freq. key for ServiceScan

		cmd = ""
		self.cmd = ""
		self.tmpstr = ""

		not_support_text = _("It seems manufacturer does not support blind scan for this tuner.")
		bin_name = getattr(self, 'binName', "")
		try:
			cmd, async_cmd, adjust_freq_override = build_scan_command(
				tunername=tunername,
				bin_name=bin_name,
				temp_start_int_freq=temp_start_int_freq,
				temp_end_int_freq=temp_end_int_freq,
				pol=pol, band=band, tab_pol=tab_pol, tab_hilow=tab_hilow,
				feid=self.feid, nim_socket=self.getNimSocket(self.feid),
				is_c_band_scan=self.is_c_band_scan,
				c_band_lo_freq=self.c_band_lo_freq,
				universal_lo_freq=self.universal_lo_freq,
				orb=orb[0],
				start_symbol=config.blindscan.start_symbol.value,
				stop_symbol=config.blindscan.stop_symbol.value,
				step_mhz_tbs5925=config.blindscan.step_mhz_tbs5925.value,
			)
		except _HardwareNotSupported:
			self.session.open(MessageBox, not_support_text, MessageBox.TYPE_WARNING)
			return
		except _ToolNotFound as e:
			self.session.open(MessageBox, _("Not found blind scan utility '%s'!") % e.tool, MessageBox.TYPE_ERROR)
			if e.abort:
				return
		else:
			if adjust_freq_override is not None:
				self.adjust_freq = adjust_freq_override
			if async_cmd:
				self.cmd = async_cmd
				self.bsTimer.stop()
				self.bsTimer.start(6000, True)
		print("[Blindscan][prepareScanData] prepared command: [%s]" % (cmd))

		self.thisRun = [] # used to check result corresponds with values used above
		self.thisRun.append(int(temp_start_int_freq))
		self.thisRun.append(int(temp_end_int_freq))
		self.thisRun.append(int(tab_hilow[band]))

		if not self.cmd:
			self.blindscan_container = eConsoleAppContainer()
			self.blindscan_container.appClosed.append(self.blindscanContainerClose)
			self.blindscan_container.dataAvail.append(self.blindscanContainerAvail)
			self.blindscan_container.execute(cmd)

		display_pol = pol # Display the correct polarisation in the MessageBox below
		if int(config.blindscan.polarization.value) == eDVBFrontendParametersSatellite.Polarisation_CircularRight:
			display_pol = _("circular right")
		elif int(config.blindscan.polarization.value) == eDVBFrontendParametersSatellite.Polarisation_CircularLeft:
			display_pol = _("circular left")
		elif int(config.blindscan.polarization.value) == eDVBFrontendParametersSatellite.Polarisation_CircularRight + 2:
			if pol == "horizontal":
				display_pol = _("circular left")
			else:
				display_pol = _("circular right")
		if display_pol == "horizontal":
			display_pol = _("horizontal")
		if display_pol == "vertical":
			display_pol = _("vertical")

		tmpmes = _("Tp count = (%d)   Scan Steps = %d of %d --%s\nSatellite: %s\nSearching: %d - %d MHz (%d - %d SR) ") %(len(self.tmp_tplist), self.running_count, self.max_count, display_pol, orb[1], status_box_start_freq, status_box_end_freq, config.blindscan.start_symbol.value, config.blindscan.stop_symbol.value)
		if not self.user_defined_lnb_scan:
			self.start_freq = self.blindscan_start_frequency # Start Freq. key for ServiceScan
			self.end_freq = self.blindscan_stop_frequency # Stop freq. key for ServiceScan
		tuner = nimmanager.nim_slots[self.feid].friendly_full_description
		tmpmes2 = _("Looking for available transponders.\n \n" + tuner + "\n \n")
		self.progress_base = tmpmes
		if self.panel:
			self.panel["post_action"].setText(tmpmes2)
			self.updateElapsed()  # renders progress line + live stopwatch
		self.blindscan_session = self.panel

	def blindscanContainerClose(self, retval):
		if self.scan_aborted:
			return
		lines = self.full_data.split('\n')
		self.full_data = "" # Clear this string so we don't get duplicates on subsequent runs
		for line in lines:
			data = line.split()
			print("[Blindscan][blindscanContainerClose] cnt:", len(data), ", data:", data)
			if len(data) >= 10: # and self.dataIsGood(data):
				if data[0] == 'OK':
					parm = eDVBFrontendParametersSatellite()
					sys = {"DVB-S": parm.System_DVB_S,
						"DVB-S2": parm.System_DVB_S2,
						"DVB-S2X": parm.System_DVB_S2}
					qam = {"QPSK": parm.Modulation_QPSK,
						"8PSK": parm.Modulation_8PSK,
						"16APSK": parm.Modulation_16APSK,
						"32APSK": parm.Modulation_32APSK}
					inv = {"INVERSION_OFF": parm.Inversion_Off,
						"INVERSION_ON": parm.Inversion_On,
						"INVERSION_AUTO": parm.Inversion_Unknown}
					fec = {"FEC_AUTO": parm.FEC_Auto,
						"FEC_1_2": parm.FEC_1_2,
						"FEC_2_3": parm.FEC_2_3,
						"FEC_3_4": parm.FEC_3_4,
						"FEC_4_5": parm.FEC_4_5,
						"FEC_5_6": parm.FEC_5_6,
						"FEC_7_8": parm.FEC_7_8,
						"FEC_8_9": parm.FEC_8_9,
						"FEC_3_5": parm.FEC_3_5,
						"FEC_9_10": parm.FEC_9_10,
						"FEC_NONE": parm.FEC_None}
					roll = {"ROLLOFF_20": parm.RollOff_alpha_0_20,
						"ROLLOFF_25": parm.RollOff_alpha_0_25,
						"ROLLOFF_35": parm.RollOff_alpha_0_35,
						"ROLLOFF_AUTO": parm.RollOff_auto}
					pilot = {"PILOT_ON": parm.Pilot_On,
						"PILOT_OFF": parm.Pilot_Off,
						"PILOT_AUTO": parm.Pilot_Unknown}
					pol = {"HORIZONTAL": parm.Polarisation_Horizontal,
						"CIRCULARRIGHT": parm.Polarisation_CircularRight,
						"CIRCULARLEFT": parm.Polarisation_CircularLeft,
						"VERTICAL": parm.Polarisation_Vertical}
					parm.orbital_position = self.orb_position
					parm.polarisation = pol[data[1]]
					parm.frequency = int(data[2])
					parm.symbol_rate = int(data[3])
					parm.system = sys[data[4]]
					parm.inversion = inv[data[5]]
					parm.pilot = pilot[data[6]]
					parm.fec = fec.get(data[7], eDVBFrontendParametersSatellite.FEC_Auto)
					parm.modulation = qam[data[8]]
					parm.rolloff = roll[data[9]]
					if parm.system == parm.System_DVB_S:
						data = data[:10] # "DVB-S" does not support MIS/PLS or T2MI so remove any values from the output of the binary file
					parm.pls_mode = getMisPlsValue(data, 10, eDVBFrontendParametersSatellite.PLS_Gold)
					parm.is_id = getMisPlsValue(data, 11, eDVBFrontendParametersSatellite.No_Stream_Id_Filter)
					parm.pls_code = getMisPlsValue(data, 12, 0)
					if hasattr(parm, "t2mi_plp_id"):
						parm.t2mi_plp_id = getMisPlsValue(data, 13, eDVBFrontendParametersSatellite.No_T2MI_PLP_Id)
					if hasattr(parm, "t2mi_pid"):
						parm.t2mi_pid = getMisPlsValue(data, 14, eDVBFrontendParametersSatellite.T2MI_Default_Pid)
					# when blindscan returns 0,0,0 then use defaults...
					if parm.pls_mode == parm.is_id == parm.pls_code == 0:
						parm.pls_mode = eDVBFrontendParametersSatellite.PLS_Gold
						parm.is_id = eDVBFrontendParametersSatellite.No_Stream_Id_Filter
					# when blindscan returns root then switch to gold
					if parm.pls_mode == eDVBFrontendParametersSatellite.PLS_Root:
						parm.pls_mode = eDVBFrontendParametersSatellite.PLS_Gold
						parm.pls_code = root2gold(parm.pls_code)
					self.tmp_tplist.append(parm)
		# Interim found-transponder list, refreshed after each step completes.
		# Shown in amber to signal the scan is still running (not the final result).
		if self.panel:
			self.panel["found"].setText(self._formatFoundList())
		try:
			if self.blindscan_container is not None:
				self.blindscan_container.sendCtrlC()
				self.blindscan_container = None
		except Exception:
			pass
		self.releaseFrontend()

		if self.scan_aborted:
			return

		if self.running_count >= self.max_count:
			self.finishScan()
		else:
			self.stepTimer.start(BLINDSCAN_STEP_SETTLE_MS, True)

	def blindscanContainerAvail(self, data_str):
		data_str = data_str.decode()
		print("[Blindscan][blindscanContainerAvail]", data_str)
		self.full_data = self.full_data + data_str

	def runStep(self):
		if self.scan_aborted:
			return
		if self.running_count >= self.max_count:
			return
		orb = self.total_list[self.running_count][0]
		pol = self.total_list[self.running_count][1]
		band = self.total_list[self.running_count][2]
		self.running_count += 1
		is_scan = (self.running_count == self.max_count)
		print("[Blindscan][runStep] %d/%d [%d][%s][%s]" % (self.running_count, self.max_count, orb[0], pol, band))
		self.prepareScanData(orb, pol, band, is_scan)

	def runNextStep(self):
		self.stepTimer.stop()
		if self.scan_aborted:
			return
		self.runStep()

	def updateElapsed(self):
		# Live stopwatch appended to the end of the first progress line. Shows total
		# elapsed blindscan time (mm:ss), independent of the current step. Driven by a
		# 1s repeating timer started in doRun() and stopped in panelClosed().
		if not self.panel:
			return
		elapsed = int(time() - self.start_time)
		timestr = _("   Elapsed %d:%02d") % (elapsed // 60, elapsed % 60)
		base = self.progress_base
		if "\n" in base:
			first, rest = base.split("\n", 1)
			text = first + timestr + "\n" + rest
		else:
			text = base + timestr
		try:
			self.panel["progress"].setText(text)
		except Exception:
			pass

	def panelClosed(self, *args):
		# Restore the config screen hidden in doRun(). Runs synchronously before any
		# follow-on panel (results) is opened, so no flash of the config screen occurs.
		self.show()
		self.elapsedTimer.stop()
		user_cancelled = bool(args) and args[0] == False
		self.panel = None
		self.blindscan_session = None
		if user_cancelled:
			self.scan_aborted = True
			self.stepTimer.stop()
			try:
				if self.blindscan_container is not None:
					self.blindscan_container.sendCtrlC()
					self.blindscan_container = None
			except Exception:
				pass
			self.releaseFrontend()
			self.tmp_tplist = []
			import gc
			gc.collect()
			self.session.openWithCallback(self.callbackNone, MessageBox, _("The blindscan run was cancelled by the user."), MessageBox.TYPE_INFO, timeout=10)
			return
		# Programmatic close from finishScan(). Data processing is already done;
		# open modal dialogs from here, inside the enigma2 session callback context.
		self._postScanDialogs()

	def finishScan(self):
		self.signaltp4 = 0
		self["key_yellow"].setText("")
		bsconfig.XML_FILE = None
		self["actions3"].setEnabled(False)

		# Record whether the binary produced any raw results before filtering.
		self._had_raw_results = bool(self.tmp_tplist)

		if self._had_raw_results:
			self.tmp_tplist = self.correctBugsCausedByDriver(self.tmp_tplist)

			if config.blindscan.lamedb.value == True:
				self.known_transponders = self.getLamedbTransponders(self.orb_position)
				self.tmp_tplist = self.removeKnownTransponders(self.tmp_tplist, self.known_transponders)
			self.known_transponders = self.getKnownTransponders(self.orb_position)
			if config.blindscan.dont_scan_known_tps.value:
				self.tmp_tplist = self.removeKnownTransponders(self.tmp_tplist, self.known_transponders)
			elif not config.blindscan.disable_sync_with_known_tps.value:
				self.tmp_tplist = self.syncWithKnownTransponders(self.tmp_tplist, self.known_transponders)

			if not config.blindscan.disable_remove_duplicate_tps.value:
				self.tmp_tplist = self.removeDuplicateTransponders(self.tmp_tplist)

			if int(config.blindscan.filter_off_adjacent_satellites.value):
				self.tmp_tplist = self.filterOffAdjacentSatellites(self.tmp_tplist, self.orb_position, int(config.blindscan.filter_off_adjacent_satellites.value))

			if not config.blindscan.scan_mis.value:
				self.tmp_tplist = [tp for tp in self.tmp_tplist if tp.is_id <= eDVBFrontendParametersSatellite.No_Stream_Id_Filter]

			if self.tmp_tplist:
				if hasattr(eDVBFrontendParametersSatellite, "No_T2MI_PLP_Id"):
					self.tmp_tplist = sorted(self.tmp_tplist, key=lambda tp: (tp.frequency, tp.is_id, tp.pls_mode, tp.pls_code, tp.t2mi_plp_id))
				else:
					self.tmp_tplist = sorted(self.tmp_tplist, key=lambda tp: (tp.frequency, tp.is_id, tp.pls_mode, tp.pls_code))

		import gc
		gc.collect()

		# Close the panel. panelClosed() fires in the enigma2 session callback
		# context and calls _postScanDialogs() to handle modal screen opens.
		if self.panel:
			self.panel.close()
		else:
			self._postScanDialogs()

	def _postScanDialogs(self):
		"""Open verify/results/message dialog. Must run inside the enigma2 session
		callback context (called from panelClosed) so that modal opens are allowed."""
		if self.tmp_tplist:
			if config.blindscan.verify_orbital_position.value:
				try:
					self.startPositionVerification()
				except Exception:
					verifyDebugException("startPositionVerification raised:")
					self.releaseFrontend()
					self.scanCompleted()
			else:
				self.scanCompleted()
		elif getattr(self, '_had_raw_results', False):
			msg = ""
			if config.blindscan.dont_scan_known_tps.value:
				msg = _("No new transponders found! \n\nOnly transponders already listed in satellites.xml \nhave been found for those search parameters!")
			if config.blindscan.lamedb.value:
				msg = _("No new transponders found! \n\nOnly transponders already listed in lamedb channel file \nhave been found for those search parameters!")
			self.session.openWithCallback(self.callbackNone, MessageBox, msg, MessageBox.TYPE_INFO, timeout=60)
		else:
			msg = _("No transponders were found for those search parameters!")
			self.session.openWithCallback(self.callbackNone, MessageBox, msg, MessageBox.TYPE_INFO, timeout=60)
			self.tmp_tplist = []

	def asyncBlindScan(self):
		self.bsTimer.stop()
		if not self.frontend:
			return
		print("[Blindscan][asyncBlindScan] closing frontend and starting blindscan")
		self.frontend.closeFrontend() # close because blindscan-s2 does not like to be open
		self.blindscan_container = eConsoleAppContainer()
		self.blindscan_container.appClosed.append(self.blindscanContainerClose)
		self.blindscan_container.dataAvail.append(self.blindscanContainerAvail)
		self.blindscan_container.execute(self.cmd)

	def releaseFrontend(self):
		if hasattr(self, 'tuner'):
			self.tuner = None
		if hasattr(self, 'frontend'):
			del self.frontend
			self.frontend = None
		if hasattr(self, 'raw_channel'):
			del self.raw_channel
			self.raw_channel = None
