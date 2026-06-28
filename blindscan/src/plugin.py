from __future__ import print_function
from . import _
from enigma import eComponentScan, eDVBFrontendParametersSatellite, eDVBSatelliteEquipmentControl, eTimer
from Components.ActionMap import ActionMap
from Components.config import config, ConfigInteger, getConfigListEntry, ConfigNothing, ConfigSelection, ConfigYesNo
from Components.ConfigList import ConfigListScreen
from Components.Label import Label
from Components.NimManager import getConfigSatlist, nimmanager
from Components.Sources.FrontendStatus import FrontendStatus
from Plugins.Plugin import PluginDescriptor
from Screens.ChoiceBox import ChoiceBox
from Screens.Console import Console
from Screens.MessageBox import MessageBox
from Screens.Screen import Screen
from Screens.ServiceScan import ServiceScan
from Tools.BoundFunction import boundFunction
from .filters import TransponderFiltering
from time import time
import os
import Dvbcsva
import Dvbcsvb
from . import bsconfig
from .bsconfig import (BOX_MODEL, BOX_NAME, check_tnap_image,
                       XML_BLINDSCAN_DIR,
                       _unsupportedNims,
                       Lastrotorposition)
from .bsresults import BlindscanResultsMixin
from .bsscreens import BlindscanState
from .bsengine import BlindscanEngineMixin
from .bsverify import BlindscanVerifyMixin, verifyDebug


class Blindscan(BlindscanEngineMixin, BlindscanVerifyMixin, BlindscanResultsMixin, ConfigListScreen, Screen, TransponderFiltering):
	skin = """
		<screen position="center,center" size="640,565" title="Blind scan" flags="wfNoBorder">
			<widget name="rotorstatus" position="5,5" size="350,25" font="Regular;20" foregroundColor="#00ffc000"/>
			<widget name="config" position="5,30" size="630,330" scrollbarMode="showOnDemand"/>
			<ePixmap pixmap="skin_default/div-h.png" position="0,365" zPosition="1" size="640,2"/>
			<widget name="description" position="5,370" size="630,125" font="Regular;19" foregroundColor="#00ffc000"/>
			<ePixmap pixmap="skin_default/div-h.png" position="0,495" zPosition="1" size="640,2"/>
			<widget name="introduction" position="0,500" size="640,20" font="Regular;18" foregroundColor="green" halign="center"/>
			<ePixmap pixmap="/usr/lib/enigma2/python/Plugins/SystemPlugins/Blindscan/images/red.png" position="0,560" size="160,2" alphatest="on"/>
			<ePixmap pixmap="/usr/lib/enigma2/python/Plugins/SystemPlugins/Blindscan/images/green.png" position="160,560" size="160,2" alphatest="on"/>
			<ePixmap pixmap="/usr/lib/enigma2/python/Plugins/SystemPlugins/Blindscan/images/yellow.png" position="320,560" size="160,2" alphatest="on" />
			<ePixmap pixmap="/usr/lib/enigma2/python/Plugins/SystemPlugins/Blindscan/images/blue.png" position="480,560" size="160,2" alphatest="on" />
			<widget name="key_red" position="0,530" zPosition="2" size="160,20" font="Regular;18" halign="center" valign="center" backgroundColor="background" foregroundColor="white" transparent="1"/>
			<widget name="key_green" position="160,530" zPosition="2" size="160,20" font="Regular;18" halign="center" valign="center" backgroundColor="background" foregroundColor="white" transparent="1"/>
			<widget name="key_yellow" position="320,530" zPosition="2" size="160,20" font="Regular;18" halign="center" valign="center" backgroundColor="background" foregroundColor="white" transparent="1" />
			<widget name="key_blue" position="480,530" zPosition="2" size="160,20" font="Regular;18" halign="center" valign="center" backgroundColor="background" foregroundColor="white" transparent="1" />
			<widget text="LOCK" source="Frontend" render="FixedLabel" zPosition="0" position="500,5" size="160,30" font="Regular;25" foregroundColor="green" transparent="1">
				<convert type="FrontendInfo">LOCK</convert>
				<convert type="ConditionalShowHide"/>
			</widget>
		</screen>
		"""
	def __init__(self, session):
		# Check if running on a TNAP image
		if not check_tnap_image():
			self.session = session
			self.show_warning()
			return
		Screen.__init__(self, session)
		self.setup_title = _("Blind Scan (TNAP-MOD)") + " for " + BOX_MODEL + " " + BOX_NAME
		Screen.setTitle(self, self.setup_title)
		self.skinName = "Blindscan"
		self.session.postScanService = self.session.nav.getCurrentlyPlayingServiceOrGroup()

		self["description"] = Label("")
		self["rotorstatus"] = Label("")
		# update sat list
		self.satList = []
		for slot in nimmanager.nim_slots:
			if slot.canBeCompatible("DVB-S"):
				self.satList.append(nimmanager.getSatListForNim(slot.slot))
			else:
				self.satList.append(None)

		# make config
		self.getCurrentTuner = None
		self.createConfig()

		self.frontend = None
		self["Frontend"] = FrontendStatus(frontend_source=lambda: self.frontend, update_interval=500)

		self.list = []
		self.status = ""
		self.onChangedEntry = []
		self.blindscan_session = None
		self.panel = None
		self.scan_aborted = False
		self.stepTimer = eTimer()
		self.stepTimer.callback.append(self.runNextStep)
		self.elapsedTimer = eTimer()
		self.elapsedTimer.callback.append(self.updateElapsed)
		self.progress_base = ""
		self.tmpstr = ""
		self.start_time = time()
		self.orb_pos = 0
		self.orb_pos_now = 0
		self.ident_note = ""
		self.position_identifier = None
		self.verify_screen = None
		self.verify_result = None
		self.verify_aborted = False
		self.detected_position = 0
		self.scan_completed_done = False
		self.is_c_band_scan = False
		self.is_c_band_scan_5750 = False
		self.is_c_band_bandstack_scan = False
####################
		self.is_Ku_band_scan = False
		self.user_defined_lnb_scan = False
		self.user_defined_lnb_lo_freq = 0
		self.suggestedPolarisation = _("vertical and horizontal")
		self.tunerEntry = None
		self.clockTimer = eTimer()
		self.statusTimer = eTimer()
		self.statusTimer.callback.append(self.setDishOrbosValue)
		self.signaltp = 0
		self.signaltp1 = 0
		self.signaltp2 = 0
		self.signaltp4 = 0
		self.freq =""
#		self.cannotrun = False
		# run command
		self.cmd = ""
		self.bsTimer = eTimer()
		self.bsTimer.callback.append(self.asyncBlindScan)

		ConfigListScreen.__init__(self, self.list, session=session, on_change=self.changedEntry)
		self["introduction"] = Label("")

		self["actions"] = ActionMap(["SetupActions"],
		{
			"cancel": self.keyCancel,
		}, -2)

		self["actions2"] = ActionMap(["ColorActions", "SetupActions"],
		{
			"ok": self.keyGo,
			"save": self.keyGo,
			"blue": self.resetDefaults,
		}, -2)
		self["actions2"].setEnabled(False)

		self["actions3"] = ActionMap(["ColorActions"],
		{
			"yellow": self.keyYellow,
		}, -2)
		self["actions3"].setEnabled(False)

		self["key_red"] = Label(_("Exit"))
		self["key_yellow"] = Label("")
		self["key_green"] = Label("")
		self["key_blue"] = Label(_(""))

		if self.scan_nims.value is not None and self.scan_nims.value != "": # self.scan_nims set in createConfig()
			self["key_green"].setText(_("Start scan"))
			self.createSetup(True)
		else:
			self["introduction"].setText(_("Please setup your tuner configuration."))

		self.i2c_mapping_table = {}
		self.nimSockets = self.ScanNimsocket()
		self.makeNimSocket()

		if bsconfig.XML_FILE is not None and os.path.exists(bsconfig.XML_FILE):
			self["key_yellow"].setText(_("Open xml file"))
			self["actions3"].setEnabled(True)
		else:
			self["actions3"].setEnabled(False)

		if not self.textHelp in self["config"].onSelectionChanged:
			self["config"].onSelectionChanged.append(self.textHelp)
		self.textHelp()
		self.changedEntry()

	def show_warning(self):
		self.session.openWithCallback(
			self.exit_plugin,
			MessageBox,
			_("This plugin is designed specifically for TNAP images and has custom dependencies that may not be available on other image types. Running it on non-TNAP images may cause system instability. Please install the appropriate version for your image type."),
			MessageBox.TYPE_ERROR
		)

	def exit_plugin(self, answer=None):
		# Simply exit the plugin
		from Screens.MessageBox import MessageBox
		self.close()

	# for summary:
	def changedEntry(self):
		for x in self.onChangedEntry:
			x()

	def getCurrentEntry(self):
		return self["config"].getCurrent() and self["config"].getCurrent()[0] or ""

	def getCurrentValue(self):
		return self["config"].getCurrent() and str(self["config"].getCurrent()[1].getText()) or ""

	def textHelp(self):
		self["description"].setText(self.getCurrentDescription())
		self.setBlueText()

	def getCurrentDescription(self):
		return self["config"].getCurrent() and len(self["config"].getCurrent()) > 2 and self["config"].getCurrent()[2] or ""

	def createSummary(self):
		from Screens.Setup import SetupSummary
		return SetupSummary

	def createConfig(self):
		self.signaltp4 = 0
		self.feinfo = None
		frontendData = None
		defaultSat = {
			"orbpos": 192,
			"system": eDVBFrontendParametersSatellite.System_DVB_S,
			"frequency": 11836,
			"inversion": eDVBFrontendParametersSatellite.Inversion_Unknown,
			"symbolrate": 27500,
			"polarization": eDVBFrontendParametersSatellite.Polarisation_Horizontal,
			"fec": eDVBFrontendParametersSatellite.FEC_Auto,
			"fec_s2": eDVBFrontendParametersSatellite.FEC_9_10,
			"modulation": eDVBFrontendParametersSatellite.Modulation_QPSK
		}

		self.service = self.session.nav.getCurrentService()
		if self.service is not None:
			self.feinfo = self.service.frontendInfo()
			frontendData = self.feinfo and self.feinfo.getAll(True)
		if frontendData is not None:
			ttype = frontendData.get("tuner_type", "UNKNOWN")
			if ttype == "DVB-S":
				defaultSat["system"] = frontendData.get("system", eDVBFrontendParametersSatellite.System_DVB_S)
				defaultSat["frequency"] = frontendData.get("frequency", 0) / 1000
				defaultSat["inversion"] = frontendData.get("inversion", eDVBFrontendParametersSatellite.Inversion_Unknown)
				defaultSat["symbolrate"] = frontendData.get("symbol_rate", 0) / 1000
				defaultSat["polarization"] = frontendData.get("polarization", eDVBFrontendParametersSatellite.Polarisation_Horizontal)
				if defaultSat["system"] == eDVBFrontendParametersSatellite.System_DVB_S2:
					defaultSat["fec_s2"] = frontendData.get("fec_inner", eDVBFrontendParametersSatellite.FEC_Auto)
					defaultSat["rolloff"] = frontendData.get("rolloff", eDVBFrontendParametersSatellite.RollOff_alpha_0_35)
					defaultSat["pilot"] = frontendData.get("pilot", eDVBFrontendParametersSatellite.Pilot_Unknown)
				else:
					defaultSat["fec"] = frontendData.get("fec_inner", eDVBFrontendParametersSatellite.FEC_Auto)
				defaultSat["modulation"] = frontendData.get("modulation", eDVBFrontendParametersSatellite.Modulation_QPSK)
				defaultSat["orbpos"] = frontendData.get("orbital_position", 0)
			if ttype != "UNKNOWN":
				self.getCurrentTuner = frontendData.get("tuner_number", None)
		del self.feinfo
		del self.service
		del frontendData

		# Persist orbital position for the active tuner so that the next time the plugin
		# is opened on a different tuner, this tuner's satellite dropdown can default to
		# the last satellite that was actually watched on it.
		if self.getCurrentTuner is not None:
			try:
				_nim_cfg = getattr(config.blindscan.last_nim_orbpos,
				                   "nim%d" % self.getCurrentTuner, None)
				if _nim_cfg is not None:
					_nim_cfg.value = defaultSat["orbpos"]
					_nim_cfg.save()
			except Exception as e:
				print("[Blindscan][createConfig] failed to save last nim orbpos: %s" % str(e))

		self.Ku_band_freq_limits = {"low": 10700, "high": 12750}
		self.universal_lo_freq = {"low": 9750, "high": 10600}
		self.c_band_freq_limits = {"low": 3000, "high": 4200, "default_low": 3400, "default_high": 4200}
		self.c_band_5750_freq_limits = {"low": 3600, "high": 4820, "default_low": 3625, "default_high": 4800}
		self.c_band_bandstack_freq_limits = {"low": 3400, "high": 4800, "default_low": 3625, "default_high": 4800}
################
		self.c_band_5750_lo_freq = 5750
		self.c_band_lo_freq = 5150
		self.tunerIfLimits = {"low": 950, "high": 2150}
		self.uni_lnb_cutoff = 11700
		self.last_user_defined_lo_freq = 0 # # Makes values sticky when changing satellite
		self.circular_lnb_lo_freq = 10750

		self.blindscan_Ku_band_start_frequency = ConfigInteger(default=config.blindscan.Ku_band_start_frequency.value, limits=(self.Ku_band_freq_limits["low"], self.Ku_band_freq_limits["high"] - 1))
		self.blindscan_Ku_band_stop_frequency = ConfigInteger(default=config.blindscan.Ku_band_stop_frequency.value, limits=(self.Ku_band_freq_limits["low"] + 1, self.Ku_band_freq_limits["high"]))
		self.blindscan_C_band_start_frequency = ConfigInteger(default=config.blindscan.C_band_start_frequency.value, limits=(self.c_band_freq_limits["low"], self.c_band_freq_limits["high"] - 1))
		self.blindscan_C_band_stop_frequency = ConfigInteger(default=config.blindscan.C_band_stop_frequency.value, limits=(self.c_band_freq_limits["low"] + 1, self.c_band_freq_limits["high"]))
		self.blindscan_C_band_5750_start_frequency = ConfigInteger(default=config.blindscan.C_band_5750_start_frequency.value, limits=(self.c_band_5750_freq_limits["low"], self.c_band_5750_freq_limits["high"] - 1))
		self.blindscan_C_band_5750_stop_frequency = ConfigInteger(default=config.blindscan.C_band_5750_stop_frequency.value, limits=(self.c_band_5750_freq_limits["low"] + 1, self.c_band_5750_freq_limits["high"]))
		self.blindscan_C_band_bandstack_start_frequency = ConfigInteger(default=config.blindscan.C_band_bandstack_start_frequency.value, limits=(self.c_band_bandstack_freq_limits["low"], self.c_band_bandstack_freq_limits["high"] - 1))
		self.blindscan_C_band_bandstack_stop_frequency = ConfigInteger(default=config.blindscan.C_band_bandstack_stop_frequency.value, limits=(self.c_band_bandstack_freq_limits["low"] + 1, self.c_band_bandstack_freq_limits["high"]))
##############

		# collect all nims which are *not* set to "nothing"
		nim_list = []
		for n in nimmanager.nim_slots:
			if not n.isCompatible("DVB-S"):
				continue
			if hasattr(n, 'isFBCLink') and n.isFBCLink():
				continue
			if n.description in _unsupportedNims: # DVB-S NIMs without blindscan hardware or software
				continue
			if n.config_mode == "nothing":
				continue
			try:
				if n.config_mode == "advanced" and int(n.config.advanced.sat[3607].lnb.value) != 0:
					continue
			except:
				pass
			if len(nimmanager.getSatListForNim(n.slot)) < 1:
				if n.config_mode in ("advanced", "simple"):
					config.Nims[n.slot].configMode.value = "nothing"
					config.Nims[n.slot].configMode.save()
				continue
			if n.config_mode in ("loopthrough", "satposdepends"):
				root_id = nimmanager.sec.getRoot(n.slot_id, int(n.config.connectedTo.value))
				if n.type == nimmanager.nim_slots[root_id].type: # check if connected from a DVB-S to DVB-S2 Nim or vice versa
					continue
			nim_list.append((str(n.slot), n.friendly_full_description))
#		self.scan_nims = ConfigSelection(choices=nim_list)

		_valid_nim_ids = [x[0] for x in nim_list]
		_default_nim = str(self.getCurrentTuner) if (
			self.getCurrentTuner is not None and
			str(self.getCurrentTuner) in _valid_nim_ids
		) else (_valid_nim_ids[0] if _valid_nim_ids else "")
		self.scan_nims = ConfigSelection(choices=nim_list, default=_default_nim)

		self.scan_satselection = []
		for slot in nimmanager.nim_slots:
			if slot.canBeCompatible("DVB-S"):
				default_sat_pos = defaultSat["orbpos"]
				if self.getCurrentTuner is not None and slot.slot != self.getCurrentTuner:
					try:
						_nim_cfg = getattr(config.blindscan.last_nim_orbpos,
						                   "nim%d" % slot.slot, None)
						if _nim_cfg is not None and _nim_cfg.value != 0:
							# Use the last orbital position saved for this specific NIM slot
							default_sat_pos = _nim_cfg.value
						elif len(nimmanager.getRotorSatListForNim(slot.slot)) and Lastrotorposition is not None and config.misc.lastrotorposition.value != 9999:
							# Rotor tuner fallback: use last rotor position
							default_sat_pos = config.misc.lastrotorposition.value
						elif self.satList[slot.slot]:
							# Last resort: use first configured satellite for this slot
							default_sat_pos = self.satList[slot.slot][0][0]
					except Exception as e:
						print("[Blindscan][createConfig] error resolving default sat for slot %d: %s" % (slot.slot, str(e)))
						if len(nimmanager.getRotorSatListForNim(slot.slot)) and Lastrotorposition is not None and config.misc.lastrotorposition.value != 9999:
							default_sat_pos = config.misc.lastrotorposition.value
				self.scan_satselection.append(getConfigSatlist(default_sat_pos, self.satList[slot.slot]))

	def getSelectedSatIndex(self, v):
		index = 0
		none_cnt = 0
		for n in self.satList:
			if self.satList[index] is None:
				none_cnt = none_cnt + 1
			if index == int(v):
				return (index - none_cnt)
			index = index + 1
		return -1

	def createSetup(self, first_start=False):
		self.list = []
		if self.scan_nims == []:
			return
		index_to_scan = int(self.scan_nims.value)
		print("[Blindscan][createSetup] ID: ", index_to_scan)

		warning_text = ""
		nim = nimmanager.nim_slots[index_to_scan]
		nimname = nim.friendly_full_description
		if BOX_MODEL.startswith('xtrend') or BOX_MODEL.startswith('vu'):
			warning_text = _("\nWARNING! Blind scan may make the tuner malfunction on a VU+ and ET receiver. A reboot afterwards may be required to return to proper tuner function.")
			if BOX_MODEL.startswith('vu') and "AVL6222" in nimname:
				warning_text = _("\nSecond slot dual tuner may not be supported blind scan.")

		self.tunerEntry = getConfigListEntry(_("Tuner"), self.scan_nims, (_('Select a tuner that is configured for the satellite you wish to search') + warning_text))
		self.list.append(self.tunerEntry)
		config.blindscan.motor_start = ConfigYesNo(default=False)
		config.blindscan.motor_start.value = False
		self.satelliteEntry = None
		self.onlyUnknownTpsEntry = None
		self.userDefinedLnbInversionEntry = None

		if nim.canBeCompatible("DVB-S"):
			if self.isRotorSatSelected():
				self.satelliteEntry = getConfigListEntry(_('Satellite'), self.scan_satselection[self.getSelectedSatIndex(index_to_scan)], _('Select the satellite you wish to search \n (Start Dish Motor After Changing Satellite)'))
			else:
				self.satelliteEntry = getConfigListEntry(_('Satellite'), self.scan_satselection[self.getSelectedSatIndex(index_to_scan)], _('Select the satellite you wish to search '))
			self.list.append(self.satelliteEntry)

			if not self.SatBandCheck():
				self["config"].list = self.list
				self["config"].l.setList(self.list)
				self["key_green"].setText("")
				self["key_blue"].setText("")
				self["actions2"].setEnabled(False)
				self["introduction"].setText(_("LNB of current satellite not compatible with plugin"))
				return
			else:
				self["introduction"].setText(_("Press Green/OK to start the scan"))

			if self.isRotorSatSelected():
				self.dishMotorEntry = getConfigListEntry(_("Start Dish Motor"), config.blindscan.motor_start, _('Set "Start Dish Motor" to "Yes" if you have changed the satellite position in this menu and wait until motor stops before starting Blindscan. ("Start Dish Motor" defaults to "No" after Dish Move Starts.)'))
				self.list.append(self.dishMotorEntry)                
			self.searchtypeEntry = getConfigListEntry(_("Search type"), config.blindscan.search_type, _('"channel scan" searches for channels and saves them to your receiver; "transponder scan" does a transponder search and displays the results allowing user to select some or all transponder. Both options save the results in satellites.xml format under /tmp'))
			self.list.append(self.searchtypeEntry)
			if self.is_c_band_scan:
				self.list.append(getConfigListEntry(_("Scan start frequency"), self.blindscan_C_band_start_frequency, _('Frequency values must be between %d MHz and %d MHz (C-band)') % (self.c_band_freq_limits["low"], self.c_band_freq_limits["high"] - 1)))
				self.list.append(getConfigListEntry(_("Scan stop frequency"), self.blindscan_C_band_stop_frequency, _('Frequency values must be between %d MHz and %d MHz (C-band)') % (self.c_band_freq_limits["low"] + 1, self.c_band_freq_limits["high"])))
			elif self.is_c_band_5750_scan:
				self.list.append(getConfigListEntry(_("Scan start frequency"), self.blindscan_C_band_5750_start_frequency, _('Frequency values must be between %d MHz and %d MHz (C-band)') % (self.c_band_5750_freq_limits["low"], self.c_band_5750_freq_limits["high"] - 1)))
				self.list.append(getConfigListEntry(_("Scan stop frequency"), self.blindscan_C_band_5750_stop_frequency, _('Frequency values must be between %d MHz and %d MHz (C-band)') % (self.c_band_5750_freq_limits["low"] + 1, self.c_band_5750_freq_limits["high"])))
			elif self.is_c_band_bandstack_scan:
				self.list.append(getConfigListEntry(_("Scan start frequency"), self.blindscan_C_band_bandstack_start_frequency, _('Frequency values must be between %d MHz and %d MHz (C-band)') % (self.c_band_bandstack_freq_limits["low"], self.c_band_bandstack_freq_limits["high"] - 1)))
				self.list.append(getConfigListEntry(_("Scan stop frequency"), self.blindscan_C_band_bandstack_stop_frequency, _('Frequency values must be between %d MHz and %d MHz (C-band)') % (self.c_band_bandstack_freq_limits["low"] + 1, self.c_band_bandstack_freq_limits["high"])))
			elif self.is_Ku_band_scan:
				self.list.append(getConfigListEntry(_("Scan start frequency"), self.blindscan_Ku_band_start_frequency, _('Frequency values must be between %d MHz and %d MHz') % (self.Ku_band_freq_limits["low"], self.Ku_band_freq_limits["high"] - 1)))
				self.list.append(getConfigListEntry(_("Scan stop frequency"), self.blindscan_Ku_band_stop_frequency, _('Frequency values must be between %d MHz and %d MHz') % (self.Ku_band_freq_limits["low"] + 1, self.Ku_band_freq_limits["high"])))
			elif self.user_defined_lnb_scan:
				self.userDefinedLnbInversionEntry = getConfigListEntry(_("LNB inversion"), config.blindscan.user_defined_lnb_inversion, _('CAUTION: Only select "inverted" if you are using an inverted LNB (i.e. an LNB where the local oscillator frequency is greater than the scan frequency). Default is "normal". Only change this if you understand why you are doing it.'))
				self.list.append(self.userDefinedLnbInversionEntry)
				if self.last_user_defined_lo_freq != self.user_defined_lnb_lo_freq: # only recreate user defined config if user defined local oscillator changed frequency when moving to another user defined LNB
					self.last_user_defined_lo_freq = self.user_defined_lnb_lo_freq
					
					# Initialize with saved values if available
					if config.blindscan.user_defined_lnb_inverted_start_freq.value > 0:
						default_inverted_start = config.blindscan.user_defined_lnb_inverted_start_freq.value
					else:
						default_inverted_start = self.user_defined_lnb_lo_freq - self.tunerIfLimits["high"]
					
					if config.blindscan.user_defined_lnb_inverted_stop_freq.value > 0:
						default_inverted_stop = config.blindscan.user_defined_lnb_inverted_stop_freq.value
					else:
						default_inverted_stop = self.user_defined_lnb_lo_freq - self.tunerIfLimits["low"]
					
					if config.blindscan.user_defined_lnb_start_freq.value > 0:
						default_start = config.blindscan.user_defined_lnb_start_freq.value
					else:
						default_start = self.user_defined_lnb_lo_freq + self.tunerIfLimits["low"]
					
					if config.blindscan.user_defined_lnb_stop_freq.value > 0:
						default_stop = config.blindscan.user_defined_lnb_stop_freq.value
					else:
						default_stop = self.user_defined_lnb_lo_freq + self.tunerIfLimits["high"]
					
					self.blindscan_user_defined_lnb_inverted_start_frequency = ConfigInteger(default=default_inverted_start, limits=(self.user_defined_lnb_lo_freq - self.tunerIfLimits["high"], self.user_defined_lnb_lo_freq - self.tunerIfLimits["low"] - 1))
					self.blindscan_user_defined_lnb_inverted_stop_frequency = ConfigInteger(default=default_inverted_stop, limits=(self.user_defined_lnb_lo_freq - self.tunerIfLimits["high"] + 1, self.user_defined_lnb_lo_freq - self.tunerIfLimits["low"]))
					self.blindscan_user_defined_lnb_start_frequency = ConfigInteger(default=default_start, limits=(self.user_defined_lnb_lo_freq + self.tunerIfLimits["low"], self.user_defined_lnb_lo_freq + self.tunerIfLimits["high"] - 1))
					self.blindscan_user_defined_lnb_stop_frequency = ConfigInteger(default=default_stop, limits=(self.user_defined_lnb_lo_freq + self.tunerIfLimits["low"] + 1, self.user_defined_lnb_lo_freq + self.tunerIfLimits["high"]))
					
				if config.blindscan.user_defined_lnb_inversion.value:
					self.list.append(getConfigListEntry(_("Scan start frequency"), self.blindscan_user_defined_lnb_inverted_start_frequency, _('Frequency values must be between %d MHz and %d MHz') % (self.user_defined_lnb_lo_freq - self.tunerIfLimits["high"], self.user_defined_lnb_lo_freq - self.tunerIfLimits["low"] - 1)))
					self.list.append(getConfigListEntry(_("Scan stop frequency"), self.blindscan_user_defined_lnb_inverted_stop_frequency, _('Frequency values must be between %d MHz and %d MHz') % (self.user_defined_lnb_lo_freq - self.tunerIfLimits["high"] + 1, self.user_defined_lnb_lo_freq - self.tunerIfLimits["low"])))
				else: # Normal LNB, not inverted
					self.list.append(getConfigListEntry(_('Scan start frequency'), self.blindscan_user_defined_lnb_start_frequency, _('Frequency values must be between %d MHz and %d MHz') % (self.user_defined_lnb_lo_freq + self.tunerIfLimits["low"], self.user_defined_lnb_lo_freq + self.tunerIfLimits["high"] - 1)))
					self.list.append(getConfigListEntry(_('Scan stop frequency'), self.blindscan_user_defined_lnb_stop_frequency, _('Frequency values must be between %d MHz and %d MHz') % (self.user_defined_lnb_lo_freq + self.tunerIfLimits["low"] + 1, self.user_defined_lnb_lo_freq + self.tunerIfLimits["high"])))

			if nim.description == 'TBS-5925':
				self.list.append(getConfigListEntry(_("Scan Step in MHz(TBS5925)"), config.blindscan.step_mhz_tbs5925, _('Smaller steps takes longer but scan is more thorough')))
			self.list.append(getConfigListEntry(_("Polarisation"), config.blindscan.polarization, _('The suggested polarisation for this satellite is "%s"') % (self.suggestedPolarisation)))
			self.list.append(getConfigListEntry(_("Scan start symbolrate"), config.blindscan.start_symbol, _('Symbol rate values are in megasymbols; enter a value between 1 and 44')))
			self.list.append(getConfigListEntry(_("Scan stop symbolrate"), config.blindscan.stop_symbol, _('Symbol rate values are in megasymbols; enter a value between 2 and 45')))
			self.list.append(getConfigListEntry(_("Clear before scan"), config.blindscan.clearallservices, _('If you select "yes" all channels on the satellite being search will be deleted before starting the current search, yes (keep feeds) means the same but hold all feed services/transponders.')))
			self.list.append(getConfigListEntry(_("Only free scan"), config.blindscan.onlyFTA, _('If you select "yes" the scan will only save channels that are not encrypted; "no" will find encrypted and non-encrypted channels.')))
			self.onlyUnknownTpsEntry = getConfigListEntry(_("Only scan unknown transponders"), config.blindscan.dont_scan_known_tps, _('If you select "yes" the scan will only search transponders not listed in satellites.xml'))
			self.list.append(self.onlyUnknownTpsEntry)
			self.list.append(getConfigListEntry(_("Don't scan lamedb transponders"), config.blindscan.lamedb,_('If you select "yes" the scan will only search transponders not listed in lamedb channel file')))
			self.list.append(getConfigListEntry(_("Filter out adjacent satellites"), config.blindscan.filter_off_adjacent_satellites, _('When a neighbouring satellite is very strong this avoids searching transponders known to be coming from the neighbouring satellite.')))
			self.list.append(getConfigListEntry(_("Verify satellite position after scan"), config.blindscan.verify_orbital_position, _('After the blind scan the strongest found transponders are tuned and the NIT broadcast by the satellite is read to confirm the dish really is pointing at the selected satellite. Recommended for motorised dishes, as DiSEqC rotors give no position feedback.')))
			if BOX_MODEL.startswith("edision"):
				self.list.append(getConfigListEntry(_("Scan MIS transponders"), config.blindscan.scan_mis, _('If you select "no" the scan will skip transponders that use Multiple Input Stream technology, which speeds up scanning in regions where these are not used.')))
			self["config"].list = self.list
			self["config"].l.setList(self.list)
			self["key_green"].setText(_("Scan"))
			self["actions2"].setEnabled(True)



	def newConfig(self):
		self.signaltp4 = 0
		cur = self["config"].getCurrent()
		print("[Blindscan][newConfig] cur is", cur)
		if config.blindscan.motor_start.value == True:
			self.createSetup()
			# The user explicitly asked to move the dish. This is the only point
			# (besides starting the scan) where it is acceptable to grab the
			# frontend and stop the running service. startDishMovingIfRotorSat()
			# allocates the tuner (setting self.tuner) and prepares the rotor.
			if self.startDishMovingIfRotorSat():
				orb_pos = self.getOrbPos()
				tps = nimmanager.getTransponders(orb_pos)
				if len(tps) >= 1:
					transponder = (tps[0][1] // 1000, tps[0][2] // 1000, tps[0][3], tps[0][4], 2, orb_pos, tps[0][5], tps[0][6], tps[0][8], tps[0][9], eDVBFrontendParametersSatellite.No_Stream_Id_Filter, eDVBFrontendParametersSatellite.PLS_Gold, eDVBFrontendParametersSatellite.PLS_Default_Gold_Code, eDVBFrontendParametersSatellite.No_T2MI_PLP_Id, eDVBFrontendParametersSatellite.T2MI_Default_Pid)
					idx_selected_sat = int(self.getSelectedSatIndex(self.scan_nims.value))
					tmp_list = [self.satList[int(self.scan_nims.value)][self.scan_satselection[idx_selected_sat].index]]
					orb = tmp_list[0][0]
					self.orb_pos_now = 3600 - orb
					self.orb_pos_now = self.orb_pos_now /10
					self.tuner.tune(transponder)
		if cur and (cur == self.tunerEntry or cur == self.satelliteEntry or cur == self.onlyUnknownTpsEntry or cur == self.userDefinedLnbInversionEntry or config.blindscan.motor_start.value == True):
			self.createSetup()
		self.setBlueText()
		config.blindscan.motor_start.value = False
		self.getSignalLock()

	def saveFrequencyValues(self):
		# Save the current frequency values to the config
		config.blindscan.Ku_band_start_frequency.value = self.blindscan_Ku_band_start_frequency.value
		config.blindscan.Ku_band_stop_frequency.value = self.blindscan_Ku_band_stop_frequency.value
		config.blindscan.C_band_start_frequency.value = self.blindscan_C_band_start_frequency.value
		config.blindscan.C_band_stop_frequency.value = self.blindscan_C_band_stop_frequency.value
		config.blindscan.C_band_5750_start_frequency.value = self.blindscan_C_band_5750_start_frequency.value
		config.blindscan.C_band_5750_stop_frequency.value = self.blindscan_C_band_5750_stop_frequency.value
		config.blindscan.C_band_bandstack_start_frequency.value = self.blindscan_C_band_bandstack_start_frequency.value
		config.blindscan.C_band_bandstack_stop_frequency.value = self.blindscan_C_band_bandstack_stop_frequency.value

		# For user defined LNB, check if we need to save those values
		if self.user_defined_lnb_scan:
			if config.blindscan.user_defined_lnb_inversion.value:
				config.blindscan.user_defined_lnb_inverted_start_freq.value = self.blindscan_user_defined_lnb_inverted_start_frequency.value
				config.blindscan.user_defined_lnb_inverted_stop_freq.value = self.blindscan_user_defined_lnb_inverted_stop_frequency.value
			else:
				config.blindscan.user_defined_lnb_start_freq.value = self.blindscan_user_defined_lnb_start_frequency.value
				config.blindscan.user_defined_lnb_stop_freq.value = self.blindscan_user_defined_lnb_stop_frequency.value
		
		# Save all the config changes
		config.blindscan.save()


	def keyLeft(self):
		ConfigListScreen.keyLeft(self)
		from threading import Timer
		ts = Timer(.05, self.newConfig)
		ts.start()

	def keyRight(self):
		ConfigListScreen.keyRight(self)
		from threading import Timer
		ts = Timer(.05, self.newConfig)
		ts.start()

	def saveConfig(self):
		for x in self["config"].list:
			x[1].save()

	def keyCancel(self):
		self.signaltp4 = 0
		self.saveConfig()
		self.saveFrequencyValues()
		if self.clockTimer:
			self.clockTimer.stop()
		if hasattr(self, 'stepTimer') and self.stepTimer:
			self.stepTimer.stop()
		self.bsTimer.stop()
		self.statusTimer.stop()
		if self.position_identifier is not None:
			self.position_identifier.cancel()
			self.position_identifier = None
		if self.verify_screen is not None:
			screen = self.verify_screen
			self.verify_screen = None
			self.verify_aborted = True
			self.scan_completed_done = True
			try:
				screen.close()
			except Exception:
				pass
		self.releaseFrontend()
		self.session.nav.playService(self.session.postScanService)
		self.close(False)

	def forceMemoryCleanup(self):
		# Force Python garbage collection
		import gc
		import os
		
		# Log memory before cleanup
		try:
			with open('/proc/meminfo', 'r') as f:
				meminfo_before = f.read()
			cached_before = 0
			for line in meminfo_before.split('\n'):
				if line.startswith('Cached:'):
					cached_before = int(line.split()[1])
					break
			
			print("[Blindscan][MemoryCleanup] Before cleanup - Cached: %d kB" % cached_before)
		except:
			print("[Blindscan][MemoryCleanup] Failed to read memory info before cleanup")
		
		# Collect all generations
		gc.collect(0)
		gc.collect(1)
		gc.collect(2)
		
		# Clear any large lists
		if hasattr(self, 'tmp_tplist'):
			self.tmp_tplist = None
		if hasattr(self, 'total_list'):
			self.total_list = None
		if hasattr(self, 'full_data'):
			self.full_data = ""
		
		# Release frontend explicitly
		self.releaseFrontend()
		
		# Try to free system memory caches
		try:
			os.system('sync')  # Flush filesystem buffers
			with open('/proc/sys/vm/drop_caches', 'w') as f:
				f.write('3')
				print("[Blindscan][MemoryCleanup] Cache drop command executed")
			
			# Verify cache was cleared by checking meminfo again
			with open('/proc/meminfo', 'r') as f:
				meminfo_after = f.read()
			cached_after = 0
			for line in meminfo_after.split('\n'):
				if line.startswith('Cached:'):
					cached_after = int(line.split()[1])
					break
			
			print("[Blindscan][MemoryCleanup] After cleanup - Cached: %d kB" % cached_after)
			print("[Blindscan][MemoryCleanup] Freed approximately %d kB of cache" % (cached_before - cached_after))
			
			# Add small delay to allow OS to reclaim memory
			from time import sleep
			sleep(1)
			
			return cached_before - cached_after  # Return the amount of memory freed
		except Exception as e:
			print("[Blindscan][MemoryCleanup] Error clearing caches: %s" % str(e))
			return 0

	def keyGo(self):
		# Clear memory before starting a new scan
		import gc
		gc.collect()
		self.signaltp4 = 1
		self.getSignalLock()
		self.saveConfig()
		print("[Blindscan][keyGo] started")
		self.start_time = time()
		# Force memory cleanup before starting
		self.forceMemoryCleanup()
		# Reinitialize necessary lists
		self.tmp_tplist = []
		self.total_list = []
		self.full_data = ""
		self.ident_note = ""
		self.scan_completed_done = False

		tab_pol = {
			eDVBFrontendParametersSatellite.Polarisation_Horizontal: "horizontal",
			eDVBFrontendParametersSatellite.Polarisation_Vertical: "vertical",
			eDVBFrontendParametersSatellite.Polarisation_CircularLeft: "circular left",
			eDVBFrontendParametersSatellite.Polarisation_CircularRight: "circular right",
			eDVBFrontendParametersSatellite.Polarisation_CircularRight + 1: "horizontal and vertical",
			eDVBFrontendParametersSatellite.Polarisation_CircularRight + 2: "circular left and circular right"
		}

		self.tmp_tplist = []
		tmp_pol = []
		tmp_band = []
		idx_selected_sat = int(self.getSelectedSatIndex(self.scan_nims.value))
		tmp_list = [self.satList[int(self.scan_nims.value)][self.scan_satselection[idx_selected_sat].index]]

		if self.is_Ku_band_scan:
			self.checkStartStopValues(self.blindscan_Ku_band_start_frequency, self.blindscan_Ku_band_stop_frequency)
			self.blindscan_start_frequency = self.blindscan_Ku_band_start_frequency.value
			self.blindscan_stop_frequency = self.blindscan_Ku_band_stop_frequency.value
		elif self.is_c_band_scan:
			self.checkStartStopValues(self.blindscan_C_band_start_frequency, self.blindscan_C_band_stop_frequency)
			self.blindscan_start_frequency = self.blindscan_C_band_start_frequency.value
			self.blindscan_stop_frequency = self.blindscan_C_band_stop_frequency.value
		elif self.is_c_band_5750_scan:
			self.checkStartStopValues(self.blindscan_C_band_5750_start_frequency, self.blindscan_C_band_5750_stop_frequency)
			self.blindscan_start_frequency = self.blindscan_C_band_5750_start_frequency.value
			self.blindscan_stop_frequency = self.blindscan_C_band_5750_stop_frequency.value
		elif self.is_c_band_bandstack_scan:
			self.checkStartStopValues(self.blindscan_C_band_bandstack_start_frequency, self.blindscan_C_band_bandstack_stop_frequency)
			self.blindscan_start_frequency = self.blindscan_C_band_bandstack_start_frequency.value
			self.blindscan_stop_frequency = self.blindscan_C_band_bandstack_stop_frequency.value
###################

		elif self.user_defined_lnb_scan:
			if config.blindscan.user_defined_lnb_inversion.value:
				self.checkStartStopValues(self.blindscan_user_defined_lnb_inverted_start_frequency, self.blindscan_user_defined_lnb_inverted_stop_frequency)
				self.blindscan_start_frequency = abs((self.blindscan_user_defined_lnb_inverted_stop_frequency.value - self.user_defined_lnb_lo_freq) * 2) + self.blindscan_user_defined_lnb_inverted_stop_frequency.value - (self.user_defined_lnb_lo_freq - self.universal_lo_freq["low"])
				self.blindscan_stop_frequency = abs((self.blindscan_user_defined_lnb_inverted_start_frequency.value - self.user_defined_lnb_lo_freq) * 2) + self.blindscan_user_defined_lnb_inverted_start_frequency.value - (self.user_defined_lnb_lo_freq - self.universal_lo_freq["low"])
			else:
				self.checkStartStopValues(self.blindscan_user_defined_lnb_start_frequency, self.blindscan_user_defined_lnb_stop_frequency)
				self.blindscan_start_frequency = self.blindscan_user_defined_lnb_start_frequency.value - (self.user_defined_lnb_lo_freq - self.universal_lo_freq["low"])
				self.blindscan_stop_frequency = self.blindscan_user_defined_lnb_stop_frequency.value - (self.user_defined_lnb_lo_freq - self.universal_lo_freq["low"])
		else:
			return

		self.checkStartStopValues(config.blindscan.start_symbol, config.blindscan.stop_symbol)

		if self.user_defined_lnb_scan:
			uni_lnb_cutoff = self.blindscan_stop_frequency
		else:
			uni_lnb_cutoff = self.uni_lnb_cutoff

		if self.blindscan_start_frequency < uni_lnb_cutoff and self.blindscan_stop_frequency > uni_lnb_cutoff:
			tmp_band = ["low", "high"]
		elif self.blindscan_start_frequency < uni_lnb_cutoff:
			tmp_band = ["low"]
		else:
			tmp_band = ["high"]

		if int(config.blindscan.polarization.value) > eDVBFrontendParametersSatellite.Polarisation_CircularRight: # must be searching both polarisations, either V and H, or R and L
			tmp_pol = ["vertical", "horizontal"]
		elif int(config.blindscan.polarization.value) == eDVBFrontendParametersSatellite.Polarisation_CircularRight:
			tmp_pol = ["vertical"]
		elif int(config.blindscan.polarization.value) == eDVBFrontendParametersSatellite.Polarisation_CircularLeft:
			tmp_pol = ["horizontal"]
		else:
			tmp_pol = [tab_pol[int(config.blindscan.polarization.value)]]

		self.doRun(tmp_list, tmp_pol, tmp_band)

	def checkStartStopValues(self, start, stop):
		# swap start and stop values if entered the wrong way round
		if start.value > stop.value:
			start.value, stop.value = (stop.value, start.value)

	def scanCompleted(self):
		# Guard against double invocation from error-recovery paths.
		if self.scan_completed_done:
			verifyDebug("scanCompleted called twice, ignoring")
			return
		self.scan_completed_done = True
		self.releaseFrontend()
		blindscanStateList = []
		for p in self.tmp_tplist:
			print("[Blindscan][scanCompleted] data: [%d][%d][%d][%d][%d][%d][%d][%d][%d][%d]" % (p.orbital_position, p.polarisation, p.frequency, p.symbol_rate, p.system, p.inversion, p.pilot, p.fec, p.modulation, p.modulation))

			pol = {p.Polarisation_Horizontal: "H",
				p.Polarisation_CircularRight: "R",
				p.Polarisation_CircularLeft: "L",
				p.Polarisation_Vertical: "V"}
			fec = {p.FEC_Auto: "Auto",
				p.FEC_1_2: "1/2",
				p.FEC_2_3: "2/3",
				p.FEC_3_4: "3/4",
				p.FEC_4_5: "4/5",
				p.FEC_5_6: "5/6",
				p.FEC_7_8: "7/8",
				p.FEC_8_9: "8/9",
				p.FEC_3_5: "3/5",
				p.FEC_9_10: "9/10",
				p.FEC_None: "None"}
			sys = {p.System_DVB_S: "DVB-S",
				p.System_DVB_S2: "DVB-S2"}
			qam = {p.Modulation_QPSK: "QPSK",
				p.Modulation_8PSK: "8PSK",
				p.Modulation_16APSK: "16APSK",
				p.Modulation_32APSK: "32APSK"}
			tp_str = "%g%s %d FEC %s %s %s" % (p.frequency / 1000.0, pol.get(p.polarisation, ""), p.symbol_rate // 1000, fec.get(p.fec, ""), sys.get(p.system, ""), qam.get(p.modulation, ""))
			if p.is_id > eDVBFrontendParametersSatellite.No_Stream_Id_Filter:
				tp_str += " MIS %d" % p.is_id
			if p.pls_code > 0:
				tp_str += " PLS Gold %d" % p.pls_code
			if hasattr(p, "t2mi_plp_id") and p.t2mi_plp_id > eDVBFrontendParametersSatellite.No_T2MI_PLP_Id:
				tp_str += " T2MI %d" % p.t2mi_plp_id
			if hasattr(p, "t2mi_pid") and hasattr(p, "t2mi_plp_id") and p.t2mi_plp_id > eDVBFrontendParametersSatellite.No_T2MI_PLP_Id:
				tp_str += " PID %d" % p.t2mi_pid
			blindscanStateList.append((tp_str, p))

		global start_time1
		global runtime
		start_time1 = self.start_time
		runtime = int(time() - self.start_time)
		self.runtime = int(time() - self.start_time)
		xml_location = self.createSatellitesXMLfile(self.tmp_tplist, XML_BLINDSCAN_DIR)
		if config.blindscan.search_type.value == "services": # Do a service scan
			self.startScan(True, self.tmp_tplist)
		else: # Display results
			self.session.openWithCallback(self.startScan, BlindscanState, _("Search completed\n%d transponders found in %d:%02d minutes.\nDetails saved in: %s") % (len(self.tmp_tplist), self.runtime / 60, self.runtime % 60, xml_location) + self.ident_note, "", blindscanStateList, True)

	def startScan(self, *retval):
		if retval[0] == False:
			return
		tuner = nimmanager.nim_slots[self.feid].friendly_full_description
		tlist = retval[1]
		networkid = 0
		flags = 0
		tmp = config.blindscan.clearallservices.value
		if tmp == "no":
			flags |= eComponentScan.scanDontRemoveUnscanned
		elif tmp == "yes":
			flags |= eComponentScan.scanRemoveServices
		elif tmp == "yes_hold_feeds":
			flags |= eComponentScan.scanRemoveServices
			flags |= eComponentScan.scanDontRemoveFeeds
		if config.blindscan.onlyFTA.value:
			flags |= eComponentScan.scanOnlyFree
		if config.blindscan.search_type.value == "transponders":
			self.session.openWithCallback(self.startScanCallback, ServiceScan, [{"transponders": tlist, "feid": self.feid, "flags": flags, "networkid": networkid, "name": BOX_NAME, "start": runtime, "tuner": tuner,"freq1": self.start_freq,  "freq2": self.end_freq,  "symbol1": config.blindscan.start_symbol.value,  "symbol2": config.blindscan.stop_symbol.value,"free": config.blindscan.onlyFTA.value }])
		if config.blindscan.search_type.value == "services":
			self.session.openWithCallback(self.startScanCallback, ServiceScan, [{"transponders": tlist, "feid": self.feid, "flags": flags, "networkid": networkid, "name": BOX_NAME, "start1": start_time1, "tuner": tuner,"freq1": self.start_freq,  "freq2": self.end_freq,  "symbol1": config.blindscan.start_symbol.value,  "symbol2": config.blindscan.stop_symbol.value, "free": config.blindscan.onlyFTA.value}])
#self.session.openWithCallback(self.startScanCallback, ServiceScan, 

	def keyYellow(self):
		if bsconfig.XML_FILE and os.path.exists(bsconfig.XML_FILE):
			self.session.open(Console, _(bsconfig.XML_FILE), ["cat %s" % bsconfig.XML_FILE])

	def resetDefaults(self):
		# Reset the simple configuration values directly
		config.blindscan.search_type.value = "transponders"
		config.blindscan.user_defined_lnb_inversion.value = False
		config.blindscan.step_mhz_tbs5925.value = 10
		config.blindscan.polarization.value = str(eDVBFrontendParametersSatellite.Polarisation_CircularRight + 1)
		config.blindscan.start_symbol.value = 1
		config.blindscan.stop_symbol.value = 60
		config.blindscan.clearallservices.value = "no"
		config.blindscan.onlyFTA.value = False
		config.blindscan.lamedb.value = False
		config.blindscan.dont_scan_known_tps.value = False
		config.blindscan.filter_off_adjacent_satellites.value = "0"
		config.blindscan.verify_orbital_position.value = True
		
		# Reset frequency values based on the current scan type
		if self.is_Ku_band_scan:
			self.blindscan_Ku_band_start_frequency.value = self.Ku_band_freq_limits["low"]
			self.blindscan_Ku_band_stop_frequency.value = self.Ku_band_freq_limits["high"]
		elif self.is_c_band_scan:
			self.blindscan_C_band_start_frequency.value = self.c_band_freq_limits["default_low"]
			self.blindscan_C_band_stop_frequency.value = self.c_band_freq_limits["default_high"]
		elif self.is_c_band_5750_scan:
			self.blindscan_C_band_5750_start_frequency.value = self.c_band_5750_freq_limits["default_low"]
			self.blindscan_C_band_5750_stop_frequency.value = self.c_band_5750_freq_limits["default_high"]
		elif self.is_c_band_bandstack_scan:
			self.blindscan_C_band_bandstack_start_frequency.value = self.c_band_5750_freq_limits["default_low"]
			self.blindscan_C_band_bandstack_stop_frequency.value = self.c_band_5750_freq_limits["default_high"]
		
		# Save all configuration settings
		config.blindscan.save()
		
		# Refresh the UI
		self.createSetup()


	def setBlueText(self):
		if not self.SatBandCheck():
			self["key_blue"].setText("")
			return
		
		# Always set blue button text if the satellite band is supported
		self["key_blue"].setText(_("Reset defaults"))
		
		# The logic below is kept for reference but won't affect the button text anymore
		try:
			if self.blindscan_Ku_band_start_frequency.value != self.Ku_band_freq_limits["low"] or \
				self.blindscan_Ku_band_stop_frequency.value != self.Ku_band_freq_limits["high"] or \
				self.blindscan_C_band_start_frequency.value != self.c_band_freq_limits["default_low"] or \
				self.blindscan_C_band_stop_frequency.value != self.c_band_freq_limits["default_high"] or \
				self.blindscan_C_band_5750_start_frequency.value != self.c_band_5750_freq_limits["default_low"] or \
				self.blindscan_C_band_5750_stop_frequency.value != self.c_band_5750_freq_limits["default_high"] or \
				self.blindscan_C_band_bandstack_start_frequency.value != self.c_band_bandstack_freq_limits["default_low"] or \
				self.blindscan_C_band_bandstack_stop_frequency.value != self.c_band_bandstack_freq_limits["default_high"] or \
				self.user_defined_lnb_scan and self.blindscan_user_defined_lnb_start_frequency.value != self.user_defined_lnb_lo_freq + self.tunerIfLimits["low"] or \
				self.user_defined_lnb_scan and self.blindscan_user_defined_lnb_stop_frequency.value != self.user_defined_lnb_lo_freq + self.tunerIfLimits["high"] or \
				self.user_defined_lnb_scan and self.blindscan_user_defined_lnb_inverted_start_frequency.value != self.user_defined_lnb_lo_freq - self.tunerIfLimits["high"] or \
				self.user_defined_lnb_scan and self.blindscan_user_defined_lnb_inverted_stop_frequency.value != self.user_defined_lnb_lo_freq - self.tunerIfLimits["low"]:
				# We're now always showing the Reset defaults text regardless of this check
				pass
		except:
			pass  # Keep the default text even if there's an error

	def SatBandCheck(self):
		# search for LNB type in Universal, C band, or user defined.
		cur_orb_pos = self.getOrbPos()
		self.is_c_band_scan = False
		self.is_c_band_5750_scan = False
		self.is_c_band_bandstack_scan = False
		self.is_Ku_band_scan = False
		self.user_defined_lnb_scan = False
		self.user_defined_lnb_lo_freq = 0
		self.suggestedPolarisation = _("vertical and horizontal")
		nim = nimmanager.nim_slots[int(self.scan_nims.value)]
		nimconfig = nim.config
		if nimconfig.configMode.getValue() == "equal":
			slotid = int(nimconfig.connectedTo.value)
			nim = nimmanager.nim_slots[slotid]
			nimconfig = nim.config
		if nimconfig.configMode.getValue() == "advanced":
			if nimconfig.advanced.sats.value in ("3605", "3606"):
				currSat = nimconfig.advanced.sat[int(nimconfig.advanced.sats.value)]
				import ast
				userSatellitesList = ast.literal_eval(currSat.userSatellitesList.getValue())
				if not cur_orb_pos in userSatellitesList:
					currSat = nimconfig.advanced.sat[cur_orb_pos]
			else:
				currSat = nimconfig.advanced.sat[cur_orb_pos]
			lnbnum = int(currSat.lnb.getValue())
			if lnbnum == 0 and nimconfig.advanced.sats.value in ("3601", "3602", "3603", "3604"):
				lnbnum = 65 + int(nimconfig.advanced.sats.value) - 3601
			currLnb = nimconfig.advanced.lnb[lnbnum]
			if isinstance(currLnb, ConfigNothing):
				return False
			lof = currLnb.lof.getValue()
			print("[Blindscan][isLNB] LNB type: ", lof)
			if lof == "universal_lnb":
				self.is_Ku_band_scan = True
				return True
			elif lof == "c_band":
				self.is_c_band_scan = True
				return True
			elif lof == "c_band_5750":
				self.is_c_band_5750_scan = True
				return True
			elif lof == "c_band_bandstack":
				self.is_c_band_bandstack_scan = True
				return True
			elif lof == "user_defined" and currLnb.lofl.value == currLnb.lofh.value and currLnb.lofl.value > 5000 and currLnb.lofl.value < 30000:
				if currLnb.lofl.value == self.circular_lnb_lo_freq and currLnb.lofh.value == self.circular_lnb_lo_freq and cur_orb_pos in (360, 560): # "circular_lnb" legacy support hack. For people using a "circular" LNB but that have their tuner set up as "user defined".
					self.user_defined_lnb_lo_freq = self.circular_lnb_lo_freq
					self.suggestedPolarisation = _("circular left/right")
				else: # normal "user_defined"
					self.user_defined_lnb_lo_freq = currLnb.lofl.value
				self.user_defined_lnb_scan = True
				print("[Blindscan][SatBandCheck] user defined local oscillator frequency: %d" % self.user_defined_lnb_lo_freq)
				return True
			elif lof == "circular_lnb": # lnb for use at positions 360 and 560
				self.user_defined_lnb_lo_freq = self.circular_lnb_lo_freq
				self.user_defined_lnb_scan = True
				self.suggestedPolarisation = _("vertical and horizontal")
				return True
			return False # LNB type not supported by this plugin
		elif nimconfig.configMode.getValue() == "simple" and nimconfig.diseqcMode.value == "single" and cur_orb_pos in (360, 560) and nimconfig.simpleDiSEqCSetCircularLNB.value:
			self.user_defined_lnb_lo_freq = self.circular_lnb_lo_freq
			self.user_defined_lnb_scan = True
			self.suggestedPolarisation = _("circular left/right")
			return True
		elif nimconfig.configMode.getValue() == "simple":
			self.is_Ku_band_scan = True
			return True
		return False # LNB type not supported by this plugin

	def getOrbPos(self):
		orb = 0
		try:
			idx_selected_sat = int(self.getSelectedSatIndex(self.scan_nims.value))
			tmp_list = [self.satList[int(self.scan_nims.value)][self.scan_satselection[idx_selected_sat].index]]
			orb = tmp_list[0][0]
		except:
			orb = -9999
			print("[Blind scan][getOrbPos] error parsing orb")
		return orb

	def startScanCallback(self, answer=True):
		self.releaseFrontend()
		self.saveFrequencyValues()  
		if answer:
			print("######---1903--Blindscan--startScanCallback -- Answered")
			self.session.nav.playService(self.session.postScanService)
			self.close(True)


	def isRotorSatSelected(self):
		# Pure predicate: report whether the currently selected satellite is a
		# rotor (motorized) satellite with usable transponders, WITHOUT touching
		# the frontend. Used by createSetup() to decide which config entries to
		# show. It must not allocate the tuner, stop the running service, or move
		# the dish - those side effects only belong to an explicit, user-initiated
		# dish move (see startDishMovingIfRotorSat / newConfig).
		try:
			orb_pos = self.getOrbPos()
			feid = int(self.scan_nims.value)
			rotorSatsForNim = nimmanager.getRotorSatListForNim(feid)
			if len(rotorSatsForNim) < 1:
				return False
			if not any(sat[0] == orb_pos for sat in rotorSatsForNim):
				return False
			if len(nimmanager.getTransponders(orb_pos)) < 1:
				return False
			return True
		except Exception as e:
			print("[Blindscan][isRotorSatSelected] error: %s" % str(e))
			return False

	def startDishMovingIfRotorSat(self):
		orb_pos = self.getOrbPos()
		self.orb_pos = 0
		self.feid = int(self.scan_nims.value)
		rotorSatsForNim = nimmanager.getRotorSatListForNim(self.feid)
		if len(rotorSatsForNim) < 1:
			self.releaseFrontend() # stop dish if moving due to previous call
			return False
		rotorSat = False
		for sat in rotorSatsForNim:
			if sat[0] == orb_pos:
				rotorSat = True
				break
		if not rotorSat:
			self.releaseFrontend() # stop dish if moving due to previous call
			return False
		tps = nimmanager.getTransponders(orb_pos)
		if len(tps) < 1:
			return False
		# freq, sr, pol, fec, inv, orb, sys, mod, roll, pilot, MIS, pls_mode, pls_code, t2mi
		transponder = (tps[0][1] // 1000, tps[0][2] // 1000, tps[0][3], tps[0][4], 2, orb_pos, tps[0][5], tps[0][6], tps[0][8], tps[0][9], eDVBFrontendParametersSatellite.No_Stream_Id_Filter, eDVBFrontendParametersSatellite.PLS_Gold, eDVBFrontendParametersSatellite.PLS_Default_Gold_Code, eDVBFrontendParametersSatellite.No_T2MI_PLP_Id, eDVBFrontendParametersSatellite.T2MI_Default_Pid)
		if not self.prepareFrontend():
			print("[Blindscan][startDishMovingIfRotorSat] self.prepareFrontend() failed")
			return False
		self.orb_pos = orb_pos
		if Lastrotorposition is not None and config.misc.lastrotorposition.value != 9999:
			self.statusTimer.stop()
			self.startStatusTimer()
		return True

	def getSignalLock(self):
		self.signaltp1 = 0
		self.signaltp2 = 0
		import time
		while self.signaltp4 == 0:
			idx_selected_sat = int(self.getSelectedSatIndex(self.scan_nims.value))
			tmp_list = [self.satList[int(self.scan_nims.value)][self.scan_satselection[idx_selected_sat].index]]
			orb = tmp_list[0][0] #2607
			orb = 3600 - orb #993
			orb = orb /10 # 99.3
			orb_pos = self.getOrbPos()
			orb_pos = 3600 - orb_pos
			orb_pos = orb_pos /10
			if self.orb_pos != 0 and self.orb_pos != config.misc.lastrotorposition.value:
				config.misc.lastrotorposition.value = self.orb_pos
				config.misc.lastrotorposition.save()
			if self.orb_pos_now != orb_pos or self.signaltp4 == 1:
				print("########1964-Blindscan---rotorstatus = None! (Break), self.orb_pos_now, orb_pos, self.signaltp4", self.orb_pos_now, orb_pos, self.signaltp4)
				break
			try:
				if self.orb_pos_now == orb_pos:
					text = _("%.1fW - %s(db)" %(orb_pos, self.getSignalStats()))
					self["rotorstatus"].setText(text)
				else:
					self["rotorstatus"].setText("")
			except:
				pass

	def getSignalStats(self):
		self.size = 0
		self.signaltp = 0
		if BOX_MODEL == "edision":
			status = "/lib/modules/5.15.0/extra/avl6261.ko"
			self.size = os.path.getsize(status)
		try:
			import time
			time.sleep(.2)	
			for x in range(10):
				if self.feid == 0:
					if BOX_MODEL != "edision":
						self.signaltp = Dvbcsva.fe.getSignalNoiseRatio() / 100
					if BOX_MODEL == "edision":
						self.signaltp = Dvbcsva.fe.getSignalNoiseRatio() / 4456.21
					if BOX_MODEL == "edision" and self.size > 100000:
						self.signaltp = Dvbcsva.fe.getSignalNoiseRatio() / 1000
				if self.feid == 1:
					if BOX_MODEL != "edision":
						self.signaltp = Dvbcsvb.fe.getSignalNoiseRatio() / 100
					if BOX_MODEL == "edision":
						self.signaltp = Dvbcsvb.fe.getSignalNoiseRatio() / 43.357 / 100
		except:
			pass
		if self.signaltp != 0:
			if self.signaltp < 0 or self.signaltp > 30: # Get rid of nonsense values
				return 0
			return ("%.2f" %(self.signaltp))
		else:
			return 0


	def OrbToStr(self, orbpos):
		if orbpos > 1800:
			orbpos = 3600 - orbpos
			return "%d.%d\xc2\xb0 W" % (orbpos / 10, orbpos % 10)
		return "%d.%d\xc2\xb0 E" % (orbpos / 10, orbpos % 10)

	def setDishOrbosValue(self):
		if self.getRotorMovingState():
			if self.orb_pos != 0 and self.orb_pos != config.misc.lastrotorposition.value:
				config.misc.lastrotorposition.value = self.orb_pos
				config.misc.lastrotorposition.save()

	def startStatusTimer(self):
		self.statusTimer.start(1000, True)

	def getRotorMovingState(self): #Sort of useless as this seems to only follow rotor timeout time.
		return eDVBSatelliteEquipmentControl.getInstance().isRotorMoving()



def BlindscanCallback(close, answer):
	if close and answer:
		close(True)


def BlindscanMain(session, close=None, **kwargs):
	# Check if running on a TNAP image
	if not check_tnap_image():
		session.open(
			MessageBox,
			_("This plugin is designed specifically for TNAP images and has custom dependencies that may not be available on other image types. Running it on non-TNAP images may cause system instability. Please install the appropriate version for your image type."),
			MessageBox.TYPE_ERROR
		)
		# Don't proceed with opening the plugin
		if close:
			close(False)
		return
	have_Support_Blindscan = False
	if nimmanager.hasNimType("DVB-S"):
		for n in nimmanager.nim_slots:
			if n.canBeCompatible("DVB-S") and n.description.startswith("Si216"):
				have_Support_Blindscan = True
				break
	if not have_Support_Blindscan:
		try:
			if 'Supports_Blind_Scan: yes' in open('/proc/bus/nim_sockets').read():
				have_Support_Blindscan = True
		except:
			pass

	if BOX_MODEL != "octagon" and have_Support_Blindscan or BOX_MODEL == "dreambox":
		menu = [(_("Utility from the manufacturer"), "manufacturer"), (_("Hardware type"), "hardware")]
		def scanType(choice):
			if choice:
				if choice[1] == "manufacturer":
					session.openWithCallback(boundFunction(BlindscanCallback, close), Blindscan)
				elif choice[1] == "hardware":
					from . import dmmBlindScan
					session.openWithCallback(boundFunction(BlindscanCallback, close), dmmBlindScan.DmmBlindscan)
		session.openWithCallback(scanType, ChoiceBox, title=_("Select type for scan:"), list=menu)
	else:
		session.openWithCallback(boundFunction(BlindscanCallback, close), Blindscan)


def BlindscanSetup(menuid, **kwargs):
	if menuid == "scan":
		return [(_("Satellite blind scan"), BlindscanMain, "blindscan", 50)]
	else:
		return []


def Plugins(**kwargs):
	if nimmanager.hasNimType("DVB-S"):
		for n in nimmanager.nim_slots:
			if n.canBeCompatible("DVB-S") and n.description not in _unsupportedNims: # DVB-S NIMs without blindscan hardware or software
				return PluginDescriptor(name=_("Blind scan"), description=_("Scan satellites for new transponders"), where=PluginDescriptor.WHERE_MENU, fnc=BlindscanSetup)
	return []
