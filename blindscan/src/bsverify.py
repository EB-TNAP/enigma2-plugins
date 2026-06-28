from __future__ import print_function
from . import _
from enigma import eDVBFrontendParametersSatellite, eTimer
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.config import config
from Components.NimManager import nimmanager
from Screens.ChoiceBox import ChoiceBox
from Screens.MessageBox import MessageBox
from Screens.Screen import Screen
from time import strftime
import fcntl
import os
import struct
from .bsconfig import Lastrotorposition

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
		<eLabel position="0,0" size="700,260" backgroundColor="white" zPosition="-2"/>
		<eLabel position="3,3" size="694,254" backgroundColor="black" zPosition="-1"/>
		<widget name="status" position="15,15" size="670,180" font="Regular;22" transparent="1"/>
		<widget name="hint" position="15,205" size="670,40" font="Regular;18" foregroundColor="#00ffc000" transparent="1"/>
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


class BlindscanVerifyMixin(object):

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
