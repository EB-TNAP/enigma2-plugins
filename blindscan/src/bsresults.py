from __future__ import print_function
from . import _
from enigma import getBoxType, eDVBFrontendParametersSatellite
from Components.About import about
from Components.config import config
from Components.NimManager import nimmanager
from time import strftime
from . import bsconfig


class BlindscanResultsMixin:

	def _displayFrequency(self, parm):
		# Read-only mirror of the frequency conversion in correctBugsCausedByDriver().
		# The scan binary reports every transponder in Ku-band (9750 LO) IF format,
		# so the interim "found so far" panel would otherwise show C-band / user-LNB
		# carriers as Ku numbers. This returns the *display* frequency in kHz for the
		# given transponder WITHOUT mutating it (the real conversion still happens once,
		# post-scan, in correctBugsCausedByDriver()).
		#
		# The same high-limit guards are kept so that boxes whose binary already returns
		# real frequencies (adjust_freq False: ustym, gbtrio4k, ...) are not converted a
		# second time, exactly as the post-scan path behaves.
		multiplier = 1000
		freq = parm.frequency
		if self.is_c_band_scan:
			if freq > (self.c_band_freq_limits["high"] * multiplier):
				freq = (self.c_band_lo_freq * multiplier) - (freq - (self.universal_lo_freq["low"] * multiplier))
		elif self.is_c_band_5750_scan:
			if freq > (self.c_band_5750_freq_limits["high"] * multiplier):
				freq = (self.c_band_5750_lo_freq * multiplier) - (freq - (self.universal_lo_freq["low"] * multiplier))
		elif self.is_c_band_bandstack_scan:
			if freq > (self.c_band_bandstack_freq_limits["high"] * multiplier):
				# Polarisation selects the LO, matching the post-scan path. Raw H/V is
				# still present here (circular remap happens later), so test against V.
				if parm.polarisation == eDVBFrontendParametersSatellite.Polarisation_Vertical:
					freq = (self.c_band_lo_freq * multiplier) - (freq - (self.universal_lo_freq["low"] * multiplier))
				else:
					freq = (self.c_band_5750_lo_freq * multiplier) - (freq - (self.universal_lo_freq["low"] * multiplier))
		elif self.user_defined_lnb_scan and self.adjust_freq:
			if config.blindscan.user_defined_lnb_inversion.value:
				freq = (self.user_defined_lnb_lo_freq * multiplier) - (freq - (self.universal_lo_freq["low"] * multiplier))
			else:
				freq = freq + ((self.user_defined_lnb_lo_freq - self.universal_lo_freq["low"]) * multiplier)
		return freq

	def _displayPolarisation(self, parm):
		# Read-only mirror of the circular-polarisation remap in correctBugsCausedByDriver(),
		# so the interim panel labels circular carriers as L/R like the final result does.
		pol = parm.polarisation
		cfg = int(config.blindscan.polarization.value)
		if cfg == eDVBFrontendParametersSatellite.Polarisation_CircularRight:
			pol = eDVBFrontendParametersSatellite.Polarisation_CircularRight
		elif cfg == eDVBFrontendParametersSatellite.Polarisation_CircularLeft:
			pol = eDVBFrontendParametersSatellite.Polarisation_CircularLeft
		elif cfg == eDVBFrontendParametersSatellite.Polarisation_CircularRight + 2:
			if pol == eDVBFrontendParametersSatellite.Polarisation_Horizontal:
				pol = eDVBFrontendParametersSatellite.Polarisation_CircularLeft
			else:
				pol = eDVBFrontendParametersSatellite.Polarisation_CircularRight
		return pol

	def _formatFoundList(self):
		# Render the transponders found so far for the interim (amber) panel.
		# Display-only: sorted by *display* frequency without mutating self.tmp_tplist.
		polmap = {eDVBFrontendParametersSatellite.Polarisation_Horizontal: "H",
			eDVBFrontendParametersSatellite.Polarisation_CircularRight: "R",
			eDVBFrontendParametersSatellite.Polarisation_CircularLeft: "L",
			eDVBFrontendParametersSatellite.Polarisation_Vertical: "V"}
		sysmap = {eDVBFrontendParametersSatellite.System_DVB_S: "DVB-S",
			eDVBFrontendParametersSatellite.System_DVB_S2: "DVB-S2"}
		qammap = {eDVBFrontendParametersSatellite.Modulation_QPSK: "QPSK",
			eDVBFrontendParametersSatellite.Modulation_8PSK: "8PSK",
			eDVBFrontendParametersSatellite.Modulation_16APSK: "16APSK",
			eDVBFrontendParametersSatellite.Modulation_32APSK: "32APSK"}
		if not self.tmp_tplist:
			return _("Scanning... no transponders found yet.")
		header = _("Found so far (scan still running): %d\n\n") % len(self.tmp_tplist)
		lines = []
		for p in sorted(self.tmp_tplist, key=lambda tp: self._displayFrequency(tp)):
			disp_freq = self._displayFrequency(p)
			disp_pol = self._displayPolarisation(p)
			line = "%g%s %d %s %s" % (disp_freq / 1000.0, polmap.get(disp_pol, ""), p.symbol_rate // 1000, sysmap.get(p.system, ""), qammap.get(p.modulation, ""))
			if p.is_id > eDVBFrontendParametersSatellite.No_Stream_Id_Filter:
				line += " MIS %d" % p.is_id
			if p.pls_code > 0:
				line += " PLS %d" % p.pls_code
			lines.append(line)
		return header + "\n".join(lines)

	def getLamedbTransponders(self, pos):
		tlist = []
		parts = []
		try:
			lamedb = open("/etc/enigma2/lamedb")
		except IOError:
			return tlist
		for line in lamedb:
			if not line:
				break
			line = line.strip()
			if line.startswith("s "):
				parts = line.replace("s ", "")
				parts = parts.split(':')
				if int(parts[4]) == int(pos - 3600):
					parm = eDVBFrontendParametersSatellite()
					parm.frequency = int(parts[0])
					parm.symbol_rate = int(parts[1])
					parm.polarisation = int(parts[2])
					parm.fec = int(parts[3])
					parm.inversion = int(parts[5])
					parm.orbital_position = pos
					try:
						parm.system = int(parts[7])
						parm.modulation = int(parts[8])
						parm.rolloff = int(parts[9])
						parm.pilot = int(parts[10])
					except:
						parm.system = eDVBFrontendParametersSatellite.System_DVB_S
						parm.modulation = eDVBFrontendParametersSatellite.Modulation_Auto
						parm.rolloff = eDVBFrontendParametersSatellite.RollOff_auto
						parm.pilot = eDVBFrontendParametersSatellite.Pilot_Unknown
					try:
						parm.is_id = int(parts[11])
						parm.pls_mode = int(parts[12])
						parm.pls_code = int(parts[13])
						parm.t2mi_plp_id = int(parts[14])
						parm.t2mi_pid = int(parts[15])
					except:
						parm.is_id = eDVBFrontendParametersSatellite.No_Stream_Id_Filter
						parm.pls_mode = eDVBFrontendParametersSatellite.PLS_Gold
						parm.pls_code = eDVBFrontendParametersSatellite.PLS_Default_Gold_Code
						parm.t2mi_plp_id = eDVBFrontendParametersSatellite.No_T2MI_PLP_Id
						parm.t2mi_pid = eDVBFrontendParametersSatellite.T2MI_Default_Pid
					tlist.append(parm)
		lamedb.close()
		return tlist

	def correctBugsCausedByDriver(self, tplist):
		multiplier = 1000
		if self.is_c_band_scan: # for some reason a c-band scan (with a Vu+) returns the transponder frequencies in Ku band format so they have to be converted back to c-band numbers before the subsequent service search
			x = 0
			for transponders in tplist:
				if tplist[x].frequency > (self.c_band_freq_limits["high"] * multiplier):
					tplist[x].frequency = (self.c_band_lo_freq * multiplier) - (tplist[x].frequency - (self.universal_lo_freq["low"] * multiplier))
				x += 1
		elif self.is_c_band_5750_scan: # for some reason a c-band scan (with a Vu+) returns the transponder frequencies in Ku band format so they have to be converted back to c-band numbers before the subsequent service search
			x = 0
			for transponders in tplist:
				if tplist[x].frequency > (self.c_band_5750_freq_limits["high"] * multiplier):
					tplist[x].frequency = (self.c_band_5750_lo_freq * multiplier) - (tplist[x].frequency - (self.universal_lo_freq["low"] * multiplier))
				x += 1
		elif self.is_c_band_bandstack_scan: # for c-band bandstacked LNB scans
			x = 0
			for transponders in tplist:
				if tplist[x].frequency > (self.c_band_bandstack_freq_limits["high"] * multiplier):
					# For c-band bandstacked LNB, polarization determines which LO to use
					if tplist[x].polarisation == eDVBFrontendParametersSatellite.Polarisation_Vertical:
						tplist[x].frequency = (self.c_band_lo_freq * multiplier) - (tplist[x].frequency - (self.universal_lo_freq["low"] * multiplier))
					else:
						tplist[x].frequency = (self.c_band_5750_lo_freq * multiplier) - (tplist[x].frequency - (self.universal_lo_freq["low"] * multiplier))
				x += 1

		elif self.user_defined_lnb_scan and self.adjust_freq:
			x = 0
			for transponders in tplist:
				if config.blindscan.user_defined_lnb_inversion.value:
					tplist[x].frequency = (self.user_defined_lnb_lo_freq * multiplier) - (tplist[x].frequency - (self.universal_lo_freq["low"] * multiplier)) # Flip it. Same as C-band
				else:
					tplist[x].frequency = tplist[x].frequency + ((self.user_defined_lnb_lo_freq - self.universal_lo_freq["low"]) * multiplier)
				x += 1

		x = 0
		for transponders in tplist:
			if int(config.blindscan.polarization.value) == eDVBFrontendParametersSatellite.Polarisation_CircularRight: # Return circular transponders to correct polarisation
				tplist[x].polarisation = eDVBFrontendParametersSatellite.Polarisation_CircularRight
			elif int(config.blindscan.polarization.value) == eDVBFrontendParametersSatellite.Polarisation_CircularLeft: # Return circular transponders to correct polarisation
				tplist[x].polarisation = eDVBFrontendParametersSatellite.Polarisation_CircularLeft
			elif int(config.blindscan.polarization.value) == eDVBFrontendParametersSatellite.Polarisation_CircularRight + 2: # Return circular transponders to correct polarisation
				if tplist[x].polarisation == eDVBFrontendParametersSatellite.Polarisation_Horizontal: # Return circular transponders to correct polarisation
					tplist[x].polarisation = eDVBFrontendParametersSatellite.Polarisation_CircularLeft
				else:
					tplist[x].polarisation = eDVBFrontendParametersSatellite.Polarisation_CircularRight
			x += 1
		return tplist

	def dataIsGood(self, data): # check output of the binary for nonsense values
		lower_freq = self.thisRun[0]
		upper_freq = self.thisRun[1]
		high_band = self.thisRun[2]
		data_freq = int(int(data[2]) / 1000)
		data_symbol = int(data[3])
		lower_symbol = (config.blindscan.start_symbol.value * 1000000) - 200000
		upper_symbol = (config.blindscan.stop_symbol.value * 1000000) + 200000

		if high_band:
			data_if_freq = abs(data_freq - self.universal_lo_freq["high"])
		elif self.is_c_band_scan and data_freq > self.c_band_freq_limits["low"] - 1 and data_freq < self.c_band_freq_limits["high"] + 1:
			data_if_freq = abs(self.c_band_lo_freq - data_freq)
		elif self.is_c_band_5750_scan and data_freq > self.c_band_5750_freq_limits["low"] - 1 and data_freq < self.c_band_5750_freq_limits["high"] + 1:
			data_if_freq = abs(self.c_band_5750_lo_freq - data_freq)
		elif self.is_c_band_bandstack_scan and data_freq > self.c_band_bandstack_freq_limits["low"] - 1 and data_freq < self.c_band_bandstack_freq_limits["high"] + 1:
			# For c-band bandstacked LNB, polarization determines which LO to use.
			# data[1] is the polarisation token from the blindscan binary output.
			if data[1] in ("VERTICAL", "CIRCULARRIGHT"):
				data_if_freq = abs(self.c_band_lo_freq - data_freq)
			else:
				data_if_freq = abs(self.c_band_5750_lo_freq - data_freq)

		elif self.user_defined_lnb_scan and not self.adjust_freq:
			data_if_freq = abs(data_freq - self.user_defined_lnb_lo_freq)
		else:
			data_if_freq = abs(data_freq - self.universal_lo_freq["low"])

		good = lower_freq <= data_if_freq <= upper_freq and lower_symbol <= data_symbol <= upper_symbol

		if not good:
			print("[Blindscan][dataIsGood] Data returned by the binary is not good...\n	Data: Frequency [%d], Symbol rate [%d]" % (int(data[2]), int(data[3])))

		return good

	def createSatellitesXMLfile(self, tp_list, save_xml_dir):
		pos = self.orb_position
		if pos > 1800:
			pos -= 3600
		if pos < 0:
			pos_name = '%dW' % (abs(int(pos)) / 10)
		else:
			pos_name = '%dE' % (abs(int(pos)) / 10)
		location = '%s/blindscan_%s_%s.xml' % (save_xml_dir, pos_name, strftime("%d-%m-%Y_%H-%M-%S"))
		tuner = nimmanager.nim_slots[self.feid].friendly_full_description
		polarisation = ['horizontal', 'vertical', 'circular left', 'circular right', 'vertical and horizontal', 'circular right and circular left']
		adjacent = ['no', 'up to 1 degree', 'up to 2 degrees', 'up to 3 degrees']
		known_txp = 'no'
		if config.blindscan.dont_scan_known_tps.value:
			known_txp = 'yes'
		xml = ['<?xml version="1.0" encoding="iso-8859-1"?>\n\n']
		xml.append('<!--\n')
		xml.append('	File created on %s\n' % (strftime("%A, %d of %B %Y, %H:%M:%S")))
		xml.append('	using %s receiver running Enigma2 image, version %s,\n' % (getBoxType(), about.getEnigmaVersionString()))
		xml.append('	build %s, with the blindscan plugin \n\n' % (about.getImageTypeString()))
		xml.append('	Search parameters:\n')
		xml.append('		%s\n' % (tuner))
		xml.append('		Satellite: %s\n' % (self.sat_name))
		xml.append('		Start frequency: %dMHz\n' % (self.start_freq))
		xml.append('		Stop frequency: %dMHz\n' % (self.end_freq))
		xml.append('		Polarization: %s\n' % (polarisation[int(config.blindscan.polarization.value)]))
		xml.append('		Lower symbol rate: %d\n' % (config.blindscan.start_symbol.value * 1000))
		xml.append('		Upper symbol rate: %d\n' % (config.blindscan.stop_symbol.value * 1000))
		xml.append('		Only save unknown tranponders: %s\n' % (known_txp))
		xml.append('		Filter out adjacent satellites: %s\n' % (adjacent[int(config.blindscan.filter_off_adjacent_satellites.value)]))
		xml.append('		Scan duration: %d seconds\n' % (self.runtime))
		xml.append('-->\n\n')
		xml.append('<satellites>\n')
		xml.append('	<sat name="%s" flags="0" position="%s">\n' % (self.sat_name.replace('&', '&amp;'), self.orb_position))
		for tp in tp_list:
			tmp_tp = []
			tmp_tp.append('\t\t<transponder')
			tmp_tp.append('frequency="%d"' % tp.frequency)
			tmp_tp.append('symbol_rate="%d"' % tp.symbol_rate)
			tmp_tp.append('polarization="%d"' % tp.polarisation)
			tmp_tp.append('fec_inner="%d"' % tp.fec)
			tmp_tp.append('system="%d"' % tp.system)
			tmp_tp.append('modulation="%d"' % tp.modulation)
			if tp.is_id > eDVBFrontendParametersSatellite.No_Stream_Id_Filter:
				tmp_tp.append('is_id="%d"' % tp.is_id)
			if tp.pls_code > 0:
				tmp_tp.append('pls_mode="%d"' % tp.pls_mode)
				tmp_tp.append('pls_code="%d"' % tp.pls_code)
			if hasattr(tp, "t2mi_plp_id") and tp.t2mi_plp_id > eDVBFrontendParametersSatellite.No_T2MI_PLP_Id:
				tmp_tp.append('t2mi_plp_id="%d"' % tp.t2mi_plp_id)
				if hasattr(tp, "t2mi_pid") and tp.t2mi_plp_id < eDVBFrontendParametersSatellite.T2MI_Default_Pid:
					tmp_tp.append('t2mi_pid="%d"' % tp.t2mi_pid)
			tmp_tp.append('/>\n')
			xml.append(' '.join(tmp_tp))
		xml.append('	</sat>\n')
		xml.append('</satellites>\n')
		f = open(location, "w")
		f.writelines(xml)
		f.close()
		self["key_yellow"].setText(_("Open xml file"))
		bsconfig.XML_FILE = location
		self["actions3"].setEnabled(True)
		return location
