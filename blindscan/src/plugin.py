from __future__ import print_function
from . import _
from enigma import eComponentScan, eConsoleAppContainer, eDVBFrontendParametersSatellite, eDVBResourceManager, eDVBSatelliteEquipmentControl, eTimer
from Components.ActionMap import ActionMap
from Components.config import config, ConfigBoolean, ConfigInteger, getConfigListEntry, ConfigNothing, ConfigSelection, ConfigYesNo
from Components.ConfigList import ConfigListScreen
from Components.Label import Label
from Components.ScrollLabel import ScrollLabel
from Components.NimManager import getConfigSatlist, nimmanager
from Components.Sources.FrontendStatus import FrontendStatus
from Components.Sources.StaticText import StaticText
from Components.TuneTest import Tuner
from Plugins.Plugin import PluginDescriptor
from Screens.ChoiceBox import ChoiceBox
from Screens.Console import Console
from Screens.MessageBox import MessageBox
from Screens.Screen import Screen
from Screens.ServiceScan import ServiceScan
from Tools.BoundFunction import boundFunction
from .filters import TransponderFiltering
from time import strftime, time
import fcntl
import os
import struct
import Dvbcsva
import Dvbcsvb
from . import bsconfig
from .bsconfig import (BOX_MODEL, BOX_NAME, check_tnap_image, root2gold,
                       getMisPlsValue,
                       XML_BLINDSCAN_DIR, BLINDSCAN_STEP_SETTLE_MS,
                       _supportNimType, _unsupportedNims, _blindscans2Nims)
from .bscommands import build_scan_command, _HardwareNotSupported, _ToolNotFound
from .bsresults import BlindscanResultsMixin

try:
	Lastrotorposition = config.misc.lastrotorposition
except:
	Lastrotorposition = None

class BlindscanState(ConfigListScreen, Screen):
	skin = """
	<screen position="center,center" size="1280,900" title="Satellite Blindscan" backgroundColor="black" flags="wfNoBorder">
		<eLabel position="0,0" size="1280,900" backgroundColor="black" zPosition="-1"/>

		<!-- outer frame: encloses header + body as one panel -->
		<eLabel position="15,20"   size="1250,2" backgroundColor="white"/>
		<eLabel position="15,789"  size="1250,2" backgroundColor="white"/>
		<eLabel position="15,20"   size="2,771"  backgroundColor="white"/>
		<eLabel position="1263,20" size="2,771"  backgroundColor="white"/>

		<!-- status / summary text (header band) -->
		<widget name="progress" position="38,34" size="1204,160" font="Regular;24" transparent="1"/>

		<!-- box / model identifier (top-right of header) -->
		<widget source="boxname" render="Label" position="848,30" size="400,36" font="Regular;28" halign="right" foregroundColor="#00909090" transparent="1"/>

		<!-- header separator (dimmer than the frame) -->
		<eLabel position="17,205" size="1246,1" backgroundColor="#00808080"/>

		<!-- vertical divider: body region only, dimmer than the frame -->
		<eLabel position="880,207" size="2,582" backgroundColor="#00808080"/>

		<!-- left column: config when finished, found while scanning -->
		<widget name="config" position="38,217" size="824,560" font="Regular;22" />
		<widget name="found"  position="38,217" size="824,560" font="Regular;20" foregroundColor="#00ffc000" transparent="1"/>

		<!-- right column: status / instructions -->
		<widget name="post_action" position="900,217" size="348,560" font="Regular;22" halign="center" transparent="1"/>

		<!-- footer hairline above the key bar -->
		<eLabel position="15,801" size="1250,1" backgroundColor="#00808080"/>

		<!-- color key bar (unchanged) -->
		<ePixmap pixmap="/usr/lib/enigma2/python/Plugins/SystemPlugins/Blindscan/images/red.png"    position="10,870"  size="140,4" alphatest="on"/>
		<ePixmap pixmap="/usr/lib/enigma2/python/Plugins/SystemPlugins/Blindscan/images/green.png"  position="170,870" size="140,4" alphatest="on"/>
		<ePixmap pixmap="/usr/lib/enigma2/python/Plugins/SystemPlugins/Blindscan/images/yellow.png" position="330,870" size="140,4" alphatest="on"/>
		<ePixmap pixmap="/usr/lib/enigma2/python/Plugins/SystemPlugins/Blindscan/images/blue.png"   position="490,870" size="140,4" alphatest="on"/>
		<widget source="key_red"    render="Label" position="10,810"  size="140,60" font="Regular;28" halign="center" transparent="1"/>
		<widget source="key_green"  render="Label" position="170,810" size="140,60" font="Regular;28" halign="center" transparent="1"/>
		<widget source="key_yellow" render="Label" position="330,810" size="140,60" font="Regular;28" halign="center" transparent="1"/>
		<widget source="key_blue"   render="Label" position="490,810" size="140,60" font="Regular;28" halign="center" transparent="1"/>
	</screen>
	"""


	def __init__(self, session, progress, post_action, tp_list, finished=False):
		Screen.__init__(self, session)
		self.skinName = ["BlindscanStateTNAP"]
		Screen.setTitle(self, _("                                           Blind scan state-" + BOX_NAME))
		self.finished = finished
		self["boxname"] = StaticText(BOX_NAME)
		self["progress"] = Label()
		self["progress"].setText(progress)
		self["post_action"] = Label()
		self["found"] = ScrollLabel("")
		self["key_red"] = StaticText(_("Cancel"))
		self["key_green"] = StaticText("")
		self["key_yellow"] = StaticText("")
		self["key_blue"] = StaticText("")

		self.configBooleanTpList = []
		self.tp_list = []
		ConfigListScreen.__init__(self, self.tp_list, session=self.session)

		self["actions"] = ActionMap(["SetupActions"],
		{
			"cancel": self.keyCancel,
		}, -2)

		self["actions2"] = ActionMap(["SetupActions", "ColorActions"],
		{
			"ok": self.scan,
			"save": self.scan,
			"green": self.scan,
			"yellow": self.selectAll,
			"blue": self.deselectAll,
		}, -2)

		if finished:
			self["post_action"].setText(_("Select transponders and press green to scan.\nPress yellow to select all transponders and blue to deselect all.\n \n \n"))
			self["key_green"].setText(_("Scan"))
			self["key_yellow"].setText(_("Select all"))
			self["key_blue"].setText(_("Deselect all"))
			self["actions2"].setEnabled(True)
			# Final results use the selectable config list; hide the interim found panel.
			self["found"].hide()
		else:
			self["post_action"].setText(post_action)
			self["actions2"].setEnabled(False)
# Progress mode: the config list is hidden and unused.
			self["config"].hide()
			# eActionMap dispatches LOWEST prio first; the first handler returning truthy
			# wins. The hidden ConfigList binds up/down/pageUp/pageDown (NavigationActions,
			# prio=1), so it outranks a higher-numbered scroller and moves an invisible
			# selection - which is exactly the "locked" behaviour. Disable that map here,
			# and keep the scroller below prio 1 as a fallback for forks that name it
			# differently.
			if "navigationActions" in self:
				self["navigationActions"].setEnabled(False)
			self["scrollactions"] = ActionMap(["NavigationActions", "DirectionActions"],
			{
				"pageUp": self["found"].pageUp,
				"pageDown": self["found"].pageDown,
				"up": self["found"].pageUp,
				"down": self["found"].pageDown,
			}, -1)

		for t in tp_list:
			cb = ConfigBoolean(default=True, descriptions={False: _("don't scan"), True: _("scan")})
			self.configBooleanTpList.append((cb, t[1]))
			self.tp_list.append(getConfigListEntry(t[0], cb))
		self["config"].list = self.tp_list
		self["config"].l.setList(self.tp_list)

	def selectAll(self):
		if self.finished:
			for i in self.configBooleanTpList:
				i[0].setValue(True)
			self["config"].setList(self["config"].getList())

	def deselectAll(self):
		if self.finished:
			for i in self.configBooleanTpList:
				i[0].setValue(False)
			self["config"].setList(self["config"].getList())

	def scan(self):
		if self.finished:
			scan_list = []
			for i in self.configBooleanTpList:
				if i[0].getValue():
					scan_list.append(i[1])
			if len(scan_list) > 0:
				self.close(True, scan_list)
			else:
				self.close(False)

	def keyCancel(self):
		self.close(False)


VERIFY_DEBUG_LOG = "/tmp/blindscan_verify.log"


def verifyDebug(message):
	# Enigma2 crash logs on some images do not capture python prints, so the
	# verification trace is also written to a file for diagnosis.
	print("[Blindscan][verify] %s" % message)
	try:
		f = open(VERIFY_DEBUG_LOG, "a")
		f.write("%s %s\n" % (strftime("%H:%M:%S"), message))
		f.close()
	except Exception:
		pass


def verifyDebugException(prefix):
	import traceback
	verifyDebug(prefix + "\n" + traceback.format_exc())


def bcdToInt(data):
	value = 0
	for byte in data:
		value = value * 100 + ((byte >> 4) & 0x0F) * 10 + (byte & 0x0F)
	return value


def parseSatelliteDeliveryDescriptor(d):
	# EN 300 468 satellite_delivery_system_descriptor payload (tag 0x43, 11 bytes)
	if len(d) < 11:
		return None
	freq_mhz = bcdToInt(d[0:4]) / 100.0 # 8 BCD digits in units of 10 kHz
	pos = bcdToInt(d[4:6]) # 4 BCD digits in units of 0.1 degree
	east = (d[6] & 0x80) != 0
	if not east:
		pos = (3600 - pos) % 3600
	return (freq_mhz, pos)


def parseNITSection(section):
	# Returns (version, section_number, last_section_number, [(freq_mhz, orbital_position), ...])
	# or None if the data is not a valid NIT-actual section.
	b = bytearray(section)
	if len(b) < 12 or b[0] != 0x40:
		return None
	section_length = ((b[1] & 0x0F) << 8) | b[2]
	total = 3 + section_length
	if total > len(b) or section_length < 9:
		return None
	version = (b[5] >> 1) & 0x1F
	section_number = b[6]
	last_section_number = b[7]
	results = []
	network_descriptors_length = ((b[8] & 0x0F) << 8) | b[9]
	i = 10 + network_descriptors_length
	crc_start = total - 4
	if i + 2 > crc_start:
		return (version, section_number, last_section_number, results)
	ts_loop_length = ((b[i] & 0x0F) << 8) | b[i + 1]
	i += 2
	loop_end = min(i + ts_loop_length, crc_start)
	while i + 6 <= loop_end:
		descriptors_length = ((b[i + 4] & 0x0F) << 8) | b[i + 5]
		j = i + 6
		descriptors_end = min(j + descriptors_length, loop_end)
		while j + 2 <= descriptors_end:
			tag = b[j]
			length = b[j + 1]
			if tag == 0x43 and j + 2 + length <= descriptors_end:
				parsed = parseSatelliteDeliveryDescriptor(b[j + 2:j + 2 + length])
				if parsed is not None:
					results.append(parsed)
			j += 2 + length
		i += 6 + descriptors_length
	return (version, section_number, last_section_number, results)


class SatVerifyState(Screen):
	skin = """
	<screen position="center,center" size="700,260" title="Verifying satellite position" flags="wfNoBorder">
		<widget name="status" position="15,15" size="670,180" font="Regular;22"/>
		<widget name="hint" position="15,205" size="670,40" font="Regular;18" foregroundColor="#00ffc000"/>
	</screen>
	"""

	def __init__(self, session, expected_name):
		Screen.__init__(self, session)
		Screen.setTitle(self, _("Verifying satellite position"))
		self.onAbort = []
		self["status"] = Label(_("Preparing to verify dish position for %s ...") % expected_name)
		self["hint"] = Label(_("Press EXIT to skip verification"))
		self["actions"] = ActionMap(["SetupActions"],
		{
			"cancel": self.keyCancel,
		}, -2)

	def setStatus(self, text):
		self["status"].setText(text)

	def keyCancel(self):
		for fnc in self.onAbort:
			fnc()


class OrbitalPositionIdentifier:
	# Tunes transponders found by the blind scan and reads the orbital position the
	# satellite broadcasts in its NIT (PID 0x10, table 0x40, descriptor 0x43).
	# DiSEqC rotors provide no position feedback, so the broadcast NIT is the only
	# available ground truth for where the dish is actually pointing.

	POLL_INTERVAL = 100 # ms
	LOCK_TIMEOUT = 40 # polls, 4 seconds; blindscan results include unlockable ghosts, so fail fast
	NIT_TIMEOUT = 120 # polls, 12 seconds; NIT-actual repetition interval is max 10 seconds
	FREQ_TOLERANCE_MHZ = 10.0
	DMX_SET_FILTER = 0x403c6f2b
	DMX_STOP = 0x6f2a
	DMX_SET_BUFFER_SIZE = 0x6f2d
	NIT_PID = 0x10

	def __init__(self, tuner, frontend, raw_channel, candidates, statusCallback, finishedCallback):
		self.tuner = tuner
		self.frontend = frontend
		self.raw_channel = raw_channel
		self.candidates = candidates
		self.statusCallback = statusCallback
		self.finishedCallback = finishedCallback
		self.aborted = False
		self.finished = False
		self.fd = -1
		self.candidate_index = -1
		self.state = "idle"
		self.poll_timer = eTimer()
		self.poll_timer.callback.append(self.poll)
		try:
			demux = self.raw_channel.reserveDemux()
		except Exception:
			demux = -1
		if demux < 0:
			demux = 0
		self.demux_path = "/dev/dvb/adapter0/demux%d" % demux
		verifyDebug("identifier created: %d candidates, demux %s" % (len(candidates), self.demux_path))

	def start(self):
		# Defer the first tune to the timer so the verify screen finishes opening
		# before any result callback can fire.
		self.state = "begin"
		self.poll_timer.start(self.POLL_INTERVAL)

	def abort(self):
		verifyDebug("aborted by user")
		self.aborted = True
		self.finish(None)

	def cancel(self):
		# Stop the identifier and release hardware without firing finishedCallback.
		# Used when the parent screen is closing and we must not trigger any
		# further UI or scan-completion callbacks.
		if self.finished:
			return
		self.finished = True
		self.poll_timer.stop()
		try:
			self.poll_timer.callback.remove(self.poll)
		except Exception:
			pass
		self.closeDemux()
		self.frontend = None
		self.raw_channel = None
		self.tuner = None
		self.candidates = []
		verifyDebug("cancelled (no callback)")

	def finish(self, result):
		if self.finished:
			return
		self.finished = True
		self.poll_timer.stop()
		try:
			self.poll_timer.callback.remove(self.poll)
		except Exception:
			pass
		self.closeDemux()
		# Drop hardware references immediately: this object can linger in a
		# reference cycle with its eTimer (a C++ object the python GC cannot
		# traverse), and a surviving raw_channel/frontend reference would keep
		# the tuner allocated forever ("no free tuner" after the plugin exits).
		self.frontend = None
		self.raw_channel = None
		self.tuner = None
		self.candidates = []
		verifyDebug("finished: result=%s" % str(result))
		try:
			self.finishedCallback(result)
		except Exception:
			verifyDebugException("finishedCallback raised:")

	def nextCandidate(self):
		self.closeDemux()
		self.candidate_index += 1
		if self.candidate_index >= len(self.candidates):
			verifyDebug("all candidates exhausted")
			self.finish(None)
			return
		parm = self.candidates[self.candidate_index]
		self.current_parm = parm
		self.current_freq_mhz = parm.frequency / 1000.0
		self.collected = []
		self.sections_seen = set()
		self.last_section_number = None
		self.nit_version = None
		self.state = "lock"
		self.ticks = 0
		verifyDebug("candidate %d/%d: %s" % (self.candidate_index + 1, len(self.candidates), self.describeTransponder(parm)))
		self.statusCallback(_("Verifying dish position: transponder %d of %d\n%s\nWaiting for tuner lock...") % (self.candidate_index + 1, len(self.candidates), self.describeTransponder(parm)))
		try:
			self.tuner.tune(self.buildTuple(parm))
		except Exception:
			verifyDebugException("tune raised:")
			self.finish(None)
			return
		self.poll_timer.start(self.POLL_INTERVAL)

	def describeTransponder(self, parm):
		pol_table = {eDVBFrontendParametersSatellite.Polarisation_Horizontal: "H",
			eDVBFrontendParametersSatellite.Polarisation_Vertical: "V",
			eDVBFrontendParametersSatellite.Polarisation_CircularLeft: "L",
			eDVBFrontendParametersSatellite.Polarisation_CircularRight: "R"}
		return "%g %s %d" % (parm.frequency / 1000.0, pol_table.get(parm.polarisation, "?"), parm.symbol_rate // 1000)

	def buildTuple(self, parm):
		tp = [parm.frequency // 1000, parm.symbol_rate // 1000, parm.polarisation, parm.fec,
			parm.inversion, parm.orbital_position, parm.system, parm.modulation, parm.rolloff,
			parm.pilot, parm.is_id, parm.pls_mode, parm.pls_code]
		if hasattr(parm, "t2mi_plp_id"):
			tp.append(parm.t2mi_plp_id)
			tp.append(getattr(parm, "t2mi_pid", eDVBFrontendParametersSatellite.T2MI_Default_Pid))
		return tuple(tp)

	def isLocked(self):
		if not self.frontend:
			return False
		status = {}
		try:
			self.frontend.getFrontendStatus(status)
		except TypeError:
			try:
				status = self.frontend.getFrontendStatus() or {}
			except Exception:
				return False
		except Exception:
			return False
		if status.get("tuner_state", "") == "LOCKED":
			return True
		return bool(status.get("tuner_locked", 0))

	def openDemux(self):
		try:
			self.fd = os.open(self.demux_path, os.O_RDWR | os.O_NONBLOCK)
		except (OSError, IOError) as e:
			verifyDebug("cannot open %s: %s" % (self.demux_path, str(e)))
			self.fd = -1
			return False
		try:
			fcntl.ioctl(self.fd, self.DMX_SET_BUFFER_SIZE, 192512)
		except Exception:
			pass
		flt = bytearray(48) # dmx_filter: filter[16] + mask[16] + mode[16]
		flt[0] = 0x40 # NIT-actual table_id
		flt[16] = 0xFF
		# dmx_sct_filter_params: u16 pid, dmx_filter, 2 pad bytes, u32 timeout, u32 flags
		params = struct.pack(str('=H48s2xII'), self.NIT_PID, bytes(flt), 0, 5) # flags = DMX_CHECK_CRC | DMX_IMMEDIATE_START
		try:
			fcntl.ioctl(self.fd, self.DMX_SET_FILTER, params)
		except Exception as e:
			verifyDebug("DMX_SET_FILTER failed on %s: %s" % (self.demux_path, str(e)))
			self.closeDemux()
			return False
		verifyDebug("NIT filter active on %s" % self.demux_path)
		return True

	def closeDemux(self):
		if self.fd >= 0:
			try:
				fcntl.ioctl(self.fd, self.DMX_STOP)
			except Exception:
				pass
			try:
				os.close(self.fd)
			except Exception:
				pass
		self.fd = -1

	def readSections(self):
		while True:
			try:
				data = os.read(self.fd, 4096)
			except (OSError, IOError):
				return
			if not data:
				return
			parsed = parseNITSection(data)
			if parsed is None:
				verifyDebug("demux delivered %d bytes, not a NIT-actual section" % len(data))
				continue
			version, section_number, last_section_number, entries = parsed
			if section_number not in self.sections_seen:
				verifyDebug("NIT section %d/%d v%d: %s" % (section_number, last_section_number, version, str(entries)))
			if self.nit_version is not None and version != self.nit_version:
				self.sections_seen = set()
				self.collected = []
			self.nit_version = version
			self.last_section_number = last_section_number
			self.sections_seen.add(section_number)
			self.collected.extend(entries)

	def allSectionsSeen(self):
		return self.last_section_number is not None and len(self.sections_seen) >= self.last_section_number + 1

	def evaluate(self, final):
		# An NIT entry whose frequency matches the tuned transponder pinpoints the
		# declared position of the transponder we are actually receiving.
		matched = set()
		for freq_mhz, pos in self.collected:
			if abs(freq_mhz - self.current_freq_mhz) <= self.FREQ_TOLERANCE_MHZ:
				matched.add(pos)
		if len(matched) == 1:
			return {"orbital_position": matched.pop(), "method": "frequency", "transponder": self.current_parm}
		if final:
			# Fallback: no frequency match, but every entry in the NIT agrees on one position.
			all_positions = set([pos for _freq, pos in self.collected])
			if len(all_positions) == 1:
				return {"orbital_position": all_positions.pop(), "method": "network", "transponder": self.current_parm}
		return None

	def poll(self):
		# Any exception escaping an eTimer callback kills the timer dispatch with
		# only an opaque "PyObject_CallObject failed" in the enigma log, leaving
		# the tuner allocated. Trap everything and end the verification cleanly.
		try:
			self.doPoll()
		except Exception:
			verifyDebugException("poll raised:")
			self.finish(None)

	def doPoll(self):
		if self.finished:
			return
		if self.state == "begin":
			self.nextCandidate()
			return
		self.ticks += 1
		if self.state == "lock":
			if self.isLocked():
				verifyDebug("locked after %d ticks" % self.ticks)
				if self.openDemux():
					self.state = "nit"
					self.ticks = 0
					self.statusCallback(_("Verifying dish position: transponder %d of %d\n%s\nLocked. Reading network information table...") % (self.candidate_index + 1, len(self.candidates), self.describeTransponder(self.current_parm)))
				else:
					self.finish(None) # demux unusable, no point trying further candidates
				return
			if self.ticks >= self.LOCK_TIMEOUT:
				verifyDebug("no lock within timeout")
				self.nextCandidate()
		elif self.state == "nit":
			self.readSections()
			if self.finished:
				return
			result = self.evaluate(False)
			if result is not None:
				self.finish(result)
				return
			if self.allSectionsSeen() or self.ticks >= self.NIT_TIMEOUT:
				verifyDebug("NIT phase over (%s), %d entries collected" % ("complete" if self.allSectionsSeen() else "timeout", len(self.collected)))
				result = self.evaluate(True)
				if result is not None:
					self.finish(result)
				else:
					self.nextCandidate()


class Blindscan(BlindscanResultsMixin, ConfigListScreen, Screen, TransponderFiltering):
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
		self.panel = self.session.openWithCallback(self.panelClosed, BlindscanState, init_progress, init_action, [])
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

	def formatOrbPos(self, pos):
		if pos > 1800:
			return "%d.%dW" % ((3600 - pos) // 10, (3600 - pos) % 10)
		return "%d.%dE" % (pos // 10, pos % 10)

	def getSatNameForPosition(self, pos):
		name = ""
		try:
			name = str(nimmanager.getSatDescription(pos))
		except Exception:
			name = ""
		if name:
			return "%s (%s)" % (name, self.formatOrbPos(pos))
		return self.formatOrbPos(pos)

	def selectVerificationCandidates(self, tplist):
		# Sample across the band instead of taking the globally strongest entries:
		# blindscan ghosts cluster in one part of the spectrum, and one provider
		# with a broken NIT should not decide the verdict. Up to 2 candidates are
		# taken from each of the low, middle and high thirds of the frequency-sorted
		# list. Within each zone, plain (non-multistream, non-T2MI) transponders
		# with the highest symbol rates are preferred: commercial backbone muxes
		# lock fast and reliably carry a NIT.
		def quality(p):
			mis = p.is_id > eDVBFrontendParametersSatellite.No_Stream_Id_Filter
			t2mi = hasattr(p, "t2mi_plp_id") and p.t2mi_plp_id > eDVBFrontendParametersSatellite.No_T2MI_PLP_Id
			return (0 if (mis or t2mi) else 1, p.symbol_rate)

		if len(tplist) <= 6:
			# Few transponders: every one of them becomes a candidate, best first.
			return sorted(tplist, key=quality, reverse=True)
		by_freq = sorted(tplist, key=lambda p: p.frequency)
		third = len(by_freq) // 3
		zones = (by_freq[:third], by_freq[third:len(by_freq) - third], by_freq[len(by_freq) - third:])
		candidates = []
		for zone in zones:
			candidates.extend(sorted(zone, key=quality, reverse=True)[:2])
		# Strongest candidates first regardless of which zone supplied them.
		return sorted(candidates, key=quality, reverse=True)

	def startPositionVerification(self):
		try:
			open(VERIFY_DEBUG_LOG, "w").close() # fresh trace per scan
		except Exception:
			pass
		candidates = self.selectVerificationCandidates(self.tmp_tplist)
		if not candidates or not self.prepareFrontend():
			verifyDebug("no candidates or no frontend, skipping verification")
			self.scanCompleted()
			return
		self.verify_result = None
		self.verify_aborted = False
		# All follow-up UI (result dialogs, scan continuation) runs from this
		# screen's close-callback. Opening dialogs directly from the identifier's
		# eTimer callback is not a safe context for session screen changes.
		self.verify_screen = self.session.openWithCallback(self.verifyScreenClosed, SatVerifyState, self.getSatNameForPosition(self.orb_position))
		self.verify_screen.onAbort.append(self.abortPositionVerification)
		self.position_identifier = OrbitalPositionIdentifier(self.tuner, self.frontend, self.raw_channel, candidates, self.verify_screen.setStatus, self.positionVerificationDone)
		self.position_identifier.start()

	def abortPositionVerification(self):
		if self.position_identifier is not None:
			self.position_identifier.abort()

	def positionVerificationDone(self, result):
		# Called from the identifier's timer context: store the outcome, free the
		# hardware, close the verify screen. Everything else happens in
		# verifyScreenClosed.
		self.verify_aborted = self.position_identifier.aborted if self.position_identifier is not None else True
		self.verify_result = result
		self.position_identifier = None
		self.releaseFrontend()
		if self.verify_screen is not None:
			screen = self.verify_screen
			self.verify_screen = None
			try:
				screen.close()
			except Exception:
				verifyDebugException("closing verify screen raised:")
				self.verifyScreenClosed()
		else:
			self.verifyScreenClosed()

	def verifyScreenClosed(self, *args):
		try:
			self.processVerificationResult()
		except Exception:
			verifyDebugException("processVerificationResult raised:")
			self.ident_note = "\n" + _("Satellite position verification failed (internal error).")
			self.scanCompleted()

	def processVerificationResult(self):
		result = self.verify_result
		expected = self.orb_position
		if result is None:
			if self.verify_aborted:
				verifyDebug("verification skipped by user")
				self.ident_note = "\n" + _("Satellite position not verified (skipped).")
				self.scanCompleted()
			else:
				verifyDebug("verification inconclusive")
				self.ident_note = "\n" + _("Satellite position could not be verified (no usable NIT data).")
				self.session.openWithCallback(self.verificationInfoClosed, MessageBox, _("The satellite position could not be verified: none of the test transponders provided usable network information.\n\nResults will be saved with the selected position %s.") % self.getSatNameForPosition(expected), MessageBox.TYPE_INFO, timeout=15)
			return
		detected = result["orbital_position"]
		self.detected_position = detected
		verifyDebug("NIT declares position %d (method: %s), selected position %d" % (detected, result["method"], expected))
		diff = abs(detected - expected)
		if diff > 1800:
			diff = 3600 - diff
		if diff <= 5: # 0.5 degree tolerance for nominal-position fuzz, e.g. 0.8W vs 1.0W
			self.ident_note = "\n" + _("Satellite position verified via NIT: %s.") % self.formatOrbPos(detected)
			# The rotor is proven to be on the commanded satellite, so make the
			# stored rotor position reflect verified reality.
			if Lastrotorposition is not None and len(nimmanager.getRotorSatListForNim(self.feid)) and config.misc.lastrotorposition.value != expected:
				config.misc.lastrotorposition.value = expected
				config.misc.lastrotorposition.save()
			self.scanCompleted()
			return
		detected_name = self.getSatNameForPosition(detected)
		expected_name = self.getSatNameForPosition(expected)
		menu = [
			(_("Relabel results to %s") % detected_name, "relabel"),
			(_("Keep results as %s") % expected_name, "keep"),
			(_("Discard scan results"), "discard")]
		self.session.openWithCallback(self.verificationMismatchChoice, ChoiceBox, title=_("Dish position mismatch!\n\nScanned as: %s\nThe satellite identifies itself as: %s\n\nWhat should be done with the %d found transponders?") % (expected_name, detected_name, len(self.tmp_tplist)), list=menu)

	def verificationInfoClosed(self, answer=None):
		self.scanCompleted()

	def verificationMismatchChoice(self, choice):
		detected = self.detected_position
		action = choice[1] if choice is not None else "keep"
		if action == "relabel":
			for p in self.tmp_tplist:
				p.orbital_position = detected
			self.orb_position = detected
			self.sat_name = self.getSatNameForPosition(detected)
			if Lastrotorposition is not None and len(nimmanager.getRotorSatListForNim(self.feid)) and config.misc.lastrotorposition.value != detected:
				config.misc.lastrotorposition.value = detected
				config.misc.lastrotorposition.save()
			self.ident_note = "\n" + _("Results relabelled to %s (identified via NIT).") % self.sat_name
			self.scanCompleted()
		elif action == "discard":
			self.tmp_tplist = []
			self.session.openWithCallback(self.callbackNone, MessageBox, _("Scan results discarded."), MessageBox.TYPE_INFO, timeout=10)
		else:
			self.ident_note = "\n" + _("Warning: satellite identified itself as %s but results were kept as %s.") % (self.getSatNameForPosition(detected), self.getSatNameForPosition(self.orb_position))
			self.scanCompleted()

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

	def releaseFrontend(self):
		if hasattr(self, 'tuner'):
			self.tuner = None
		if hasattr(self, 'frontend'):
			del self.frontend
			self.frontend = None
		if hasattr(self, 'raw_channel'):
			del self.raw_channel
			self.raw_channel = None


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
