from __future__ import print_function
import ctypes
import fcntl
import glob
import os
import time
from collections import deque

from . import _
from .bsconfig import BOX_MODEL
from Components.NimManager import nimmanager

# ---------------------------------------------------------------------------
# Raw DVB frontend ioctl definitions.
#
# This is the same ioctl interface Dvbcsva.py / Dvbcsvb.py already use
# (and that bsui.getSignalStats() already leans on for rotor-status SNR).
# The difference here is lifecycle: those scripts open the device node
# ONCE at import time and keep a single global fd forever, which is why
# a widget built directly on them can go stale or throw on a box where
# frontend0/1 isn't available yet at plugin load. Everything below opens
# lazily, re-opens if a read ever fails, and never raises out of read().
# ---------------------------------------------------------------------------

_IOC_NRBITS = 8
_IOC_TYPEBITS = 8
_IOC_SIZEBITS = 14
_IOC_NRSHIFT = 0
_IOC_TYPESHIFT = _IOC_NRSHIFT + _IOC_NRBITS
_IOC_SIZESHIFT = _IOC_TYPESHIFT + _IOC_TYPEBITS
_IOC_DIRSHIFT = _IOC_SIZESHIFT + _IOC_SIZEBITS
_IOC_READ = 2


def _IOR(type_, nr, size):
	return (
		ctypes.c_int32(_IOC_READ << _IOC_DIRSHIFT).value |
		ctypes.c_int32(ord(type_) << _IOC_TYPESHIFT).value |
		ctypes.c_int32(nr << _IOC_NRSHIFT).value |
		ctypes.c_int32(ctypes.sizeof(size) << _IOC_SIZESHIFT).value
	)


fe_status_t = ctypes.c_uint
FE_HAS_SIGNAL = 0x01
FE_HAS_CARRIER = 0x02
FE_HAS_VITERBI = 0x04
FE_HAS_SYNC = 0x08
FE_HAS_LOCK = 0x10

FE_READ_STATUS = _IOR('o', 69, fe_status_t)
FE_READ_SIGNAL_STRENGTH = _IOR('o', 71, ctypes.c_uint16)
FE_READ_SNR = _IOR('o', 72, ctypes.c_uint16)

# --- DVB API v5 property interface -----------------------------------------
# The legacy v3 ioctls above are answered by the Octagon (HiSilicon) blob
# drivers, but the open-source AVL6261 driver on the Edision boxes only
# fills in the v5 statistics properties - v3 reads come back 0/error, which
# showed up as a permanent "S: 0%" on the osmio4k. Same driver split the
# ServiceScan FESignalReader already handles with its sticky api5_seen flag;
# the reader below mirrors that approach.

DTV_STAT_SIGNAL_STRENGTH = 62
DTV_STAT_CNR = 63

FE_SCALE_NOT_AVAILABLE = 0
FE_SCALE_DECIBEL = 1
FE_SCALE_RELATIVE = 2


class _DtvStats(ctypes.Structure):
	_pack_ = 1
	_fields_ = [("scale", ctypes.c_uint8), ("value", ctypes.c_int64)]


class _DtvFeStats(ctypes.Structure):
	_pack_ = 1
	_fields_ = [("len", ctypes.c_uint8), ("stat", _DtvStats * 4)]


class _DtvPropertyBuffer(ctypes.Structure):
	_pack_ = 1
	_fields_ = [
		("data", ctypes.c_uint8 * 32),
		("len", ctypes.c_uint32),
		("reserved1", ctypes.c_uint32 * 3),
		("reserved2", ctypes.c_void_p),
	]


class _DtvPropertyUnion(ctypes.Union):
	_pack_ = 1
	_fields_ = [
		("data", ctypes.c_uint32),
		("st", _DtvFeStats),
		("buffer", _DtvPropertyBuffer),
	]


class _DtvProperty(ctypes.Structure):
	_pack_ = 1  # struct dtv_property is __attribute__((packed)) in the kernel
	_fields_ = [
		("cmd", ctypes.c_uint32),
		("reserved", ctypes.c_uint32 * 3),
		("u", _DtvPropertyUnion),
		("result", ctypes.c_int),
	]


class _DtvProperties(ctypes.Structure):
	# NOT packed - matches the kernel's struct dtv_properties
	_fields_ = [("num", ctypes.c_uint32), ("props", ctypes.POINTER(_DtvProperty))]


FE_GET_PROPERTY = _IOR('o', 83, _DtvProperties)


class _RawFrontendReader(object):
	"""
	Minimal read-only poller for a single /dev/dvb/adapterX/frontendY node.
	Deliberately does NOT tune or configure anything - status/strength/SNR
	reads are non-exclusive on essentially every DVB driver, so this can
	run concurrently with whatever else (enigma2's own eDVBFrontend,
	blindscan's raw_channel, ...) is actually driving the tuner.
	"""

	def __init__(self, device_path):
		self.device_path = device_path
		self._fd = None
		# Sticky driver classification, mirroring FESignalReader in
		# ServiceScan: once the v5 property interface returns a valid
		# statistic on this frontend, prefer it from then on (the Edision
		# AVL6261 case). Octagon blobs never validate here, so they stay
		# on the legacy path with at most an occasional cheap v5 probe.
		self._prefer_api5 = False

	def _ensure_open(self):
		if self._fd is not None:
			return True
		try:
			self._fd = os.open(self.device_path, os.O_RDONLY | os.O_NONBLOCK)
			return True
		except OSError:
			self._fd = None
			return False

	def _ioctl(self, request, c_type):
		if not self._ensure_open():
			return None
		buf = c_type()
		try:
			fcntl.ioctl(self._fd, request, buf)
			return buf.value
		except OSError:
			# fd went stale (device reset, adapter reinit, etc) - drop it,
			# next read() will transparently reopen.
			try:
				os.close(self._fd)
			except OSError:
				pass
			self._fd = None
			return None

	def _read_api5_stats(self):
		"""
		FE_GET_PROPERTY for DTV_STAT_SIGNAL_STRENGTH + DTV_STAT_CNR.
		Returns (sig_raw, snr_raw, snr_db, any_valid) where:
		  sig_raw -- 0-65535 (native for FE_SCALE_RELATIVE; FE_SCALE_DECIBEL
		             dBm readings are mapped -100..-40dBm -> 0..65535 so the
		             rest of the pipeline needs no changes)
		  snr_raw -- 0-65535 if the driver reports CNR as RELATIVE (goes
		             through the usual per-box snr_raw_to_db calibration)
		  snr_db  -- direct dB if the driver reports CNR as DECIBEL
		             (0.001dB units), bypassing calibration entirely
		  any_valid -- True if at least one stat had a usable scale; this
		             is what latches _prefer_api5
		Returns None wholesale if the ioctl itself failed (blob drivers
		without v5 support land here).
		"""
		if not self._ensure_open():
			return None
		props = (_DtvProperty * 2)()
		props[0].cmd = DTV_STAT_SIGNAL_STRENGTH
		props[1].cmd = DTV_STAT_CNR
		dtv = _DtvProperties()
		dtv.num = 2
		dtv.props = ctypes.cast(props, ctypes.POINTER(_DtvProperty))
		try:
			fcntl.ioctl(self._fd, FE_GET_PROPERTY, dtv)
		except (OSError, IOError):
			return None
		sig_raw = snr_raw = snr_db = None
		any_valid = False
		st = props[0].u.st
		if st.len > 0:
			scale, val = st.stat[0].scale, st.stat[0].value
			if scale == FE_SCALE_RELATIVE:
				sig_raw = max(0, min(65535, int(val)))
				any_valid = True
			elif scale == FE_SCALE_DECIBEL:
				frac = (val / 1000.0 + 100.0) / 60.0  # -100dBm..-40dBm
				sig_raw = int(max(0.0, min(1.0, frac)) * 65535)
				any_valid = True
		st = props[1].u.st
		if st.len > 0:
			scale, val = st.stat[0].scale, st.stat[0].value
			if scale == FE_SCALE_DECIBEL:
				db = val / 1000.0
				if 0 < db <= 30:  # same nonsense guard as snr_raw_to_db
					snr_db = db
				any_valid = True
			elif scale == FE_SCALE_RELATIVE:
				snr_raw = max(0, min(65535, int(val)))
				any_valid = True
		return sig_raw, snr_raw, snr_db, any_valid

	def read(self):
		"""
		Returns a dict:
		  available -- False if the device node couldn't be opened/queried at all
		  locked    -- True if FE_HAS_LOCK is set
		  signal    -- raw 0-65535 AGC-derived strength, or None if unreadable
		  snr       -- raw 0-65535 SNR/quality, or None if unreadable
		  snr_db    -- direct dB figure when the driver reports CNR on the
		               v5 DECIBEL scale (Edision AVL6261); None otherwise,
		               in which case snr goes through per-box calibration
		signal/snr are read the moment RF is present on the LNB feed, even
		with no demod lock, which is what makes dish-movement visible.
		"""
		status = self._ioctl(FE_READ_STATUS, fe_status_t)
		signal = None
		snr = None
		snr_db = None
		if not self._prefer_api5:
			signal = self._ioctl(FE_READ_SIGNAL_STRENGTH, ctypes.c_uint16)
			snr = self._ioctl(FE_READ_SNR, ctypes.c_uint16)
		if self._prefer_api5 or not signal:
			# legacy path dead (Edision) or reporting zero - probe v5.
			# A genuine 0-signal on a legacy-only blob costs one extra
			# ioctl per tick that returns an error; harmless.
			v5 = self._read_api5_stats()
			if v5 is not None:
				v5_sig, v5_snr_raw, v5_snr_db, any_valid = v5
				if any_valid:
					self._prefer_api5 = True
					if v5_sig is not None:
						signal = v5_sig
					if v5_snr_db is not None:
						snr_db = v5_snr_db
					elif v5_snr_raw is not None:
						snr = v5_snr_raw
		return {
			"available": status is not None or signal is not None,
			"locked": bool(status is not None and (status & FE_HAS_LOCK)),
			"signal": signal,
			"snr": snr,
			"snr_db": snr_db,
		}

	def close(self):
		if self._fd is not None:
			try:
				os.close(self._fd)
			except OSError:
				pass
			self._fd = None


class DishSignalMonitor(object):
	"""
	Tracks raw signal/SNR for every DVB-S capable NIM slot, keyed by slot
	number, so multiple tuners can be shown side by side regardless of
	which one (if any) the blindscan engine currently has allocated.

	Assumes the single-adapter/multi-frontend layout used by the
	TNAP-supported boxes (slot N -> /dev/dvb/adapter0/frontendN), matching
	Michael's own Dvbcsva.py (frontend0) / Dvbcsvb.py (frontend1). Falls
	back to searching other adapter numbers for boxes wired differently.
	"""

	def __init__(self):
		self._readers = {}  # slot number -> _RawFrontendReader
		self._history = {}  # slot number -> deque of recent raw signal values (or None)
		for slot in nimmanager.nim_slots:
			if not slot.canBeCompatible("DVB-S"):
				continue
			path = self._find_device_path(slot.slot)
			if path:
				self._readers[slot.slot] = _RawFrontendReader(path)
				self._history[slot.slot] = deque(maxlen=_HISTORY_MAXLEN)

	def _find_device_path(self, slot_number):
		candidate = "/dev/dvb/adapter0/frontend%d" % slot_number
		if os.path.exists(candidate):
			return candidate
		for path in sorted(glob.glob("/dev/dvb/adapter*/frontend%d" % slot_number)):
			return path
		return None

	def slots(self):
		return sorted(self._readers.keys())

	def read(self, slot_number):
		"""
		Reads the current state AND records it into that slot's rolling
		history (used by sparkline()). Call this once per poll tick per
		slot - format_dish_monitor_text() already does this for you.
		"""
		reader = self._readers.get(slot_number)
		if reader is None:
			return None
		reading = reader.read()
		hist = self._history.get(slot_number)
		if hist is not None:
			hist.append(reading["signal"] if reading["available"] else None)
		return reading

	def read_all(self):
		return dict((slot, self.read(slot)) for slot in self.slots())

	def sparkline(self, slot_number, width=12):
		"""
		Ascii trend line of the last `width` samples recorded via read(),
		normalized to that window's own min/max (same approach as
		analyze_dish_log.py's sparkline) so it shows relative movement
		rather than absolute signal level. Real-world captures showed the
		underlying driver only updates ~1-2 times/sec, so this is a
		genuinely meaningful trend over a few seconds, not just noise.
		"""
		hist = self._history.get(slot_number)
		if not hist:
			return "?" * width
		vals = list(hist)[-width:]
		if len(vals) < width:
			vals = [None] * (width - len(vals)) + vals
		present = [v for v in vals if v is not None]
		if not present:
			return "?" * width
		lo, hi = min(present), max(present)
		span = (hi - lo) or 1
		out = []
		for v in vals:
			if v is None:
				out.append(" ")
				continue
			idx = int((v - lo) / span * (len(_SPARK_CHARS) - 1))
			out.append(_SPARK_CHARS[idx])
		return "".join(out)

	def close(self):
		for reader in self._readers.values():
			reader.close()
		self._readers = {}
		self._history = {}


_TUNER_LETTERS = "ABCDEFGH"
_BAR_WIDTH = 10
_HISTORY_MAXLEN = 30
_SPARK_CHARS = " .-:=+*#%@"

_AVL6261_MODULE_PATH = "/lib/modules/5.15.0/extra/avl6261.ko"


def signal_raw_to_pct(raw_signal):
	"""Raw 0-65535 AGC strength -> 0-100.0 percent, or None."""
	if raw_signal is None:
		return None
	return raw_signal / 65535.0 * 100.0


def snr_raw_to_db(raw_snr, slot):
	"""
	Best-effort conversion of the raw FE_READ_SNR value to an actual dB
	figure. Mirrors the per-box calibration bsui.py's getSignalStats()
	already uses for the rotor-status readout (which only ever looked at
	slot 0 / slot 1 via Dvbcsva / Dvbcsvb). Slots beyond that fall back
	to the generic (non-edision) divisor.
	"""
	if raw_snr is None:
		return None
	if BOX_MODEL == "edision":
		divisor = 4456.21 if slot == 0 else 4335.7  # 43.357 * 100, mirrors the Dvbcsvb path
		try:
			if slot == 0 and os.path.getsize(_AVL6261_MODULE_PATH) > 100000:
				divisor = 1000.0
		except OSError:
			pass
	else:
		divisor = 100.0
	db = raw_snr / divisor
	if db < 0 or db > 30:  # same "get rid of nonsense values" guard as getSignalStats()
		return None
	return db


_snr_raw_to_db = snr_raw_to_db  # backward-compat alias


def _bar(raw_value, width=_BAR_WIDTH):
	if raw_value is None:
		return "?" * width
	filled = int((raw_value / 65535.0) * width)
	filled = max(0, min(width, filled))
	return "#" * filled + "-" * (width - filled)


def format_dish_monitor_text(monitor, history_width=0):
	"""
	One line per DVB-S tuner slot, e.g. (history_width=12):

	  Tuner A  LOCK  S: 78% [#######---] Q: 12.4dB  |...-:=+*#%@=|
	  Tuner B  ....  S:  0% [----------] Q:   --dB  |            |

	The trailing |...| block, shown only when history_width > 0, is a
	rolling trend of recent samples (see DishSignalMonitor.sparkline) -
	pass history_width=0 (default) for narrow columns that can't afford
	the extra characters; the base line is fixed-width either way so it
	can never wrap or overflow regardless of tuner state.
	"""
	slots = monitor.slots()
	if not slots:
		return _("No DVB-S tuners detected.")

	lines = []
	for slot in slots:
		letter = _TUNER_LETTERS[slot] if slot < len(_TUNER_LETTERS) else str(slot)
		reading = monitor.read(slot)  # also records this sample into history
		if reading is None or not reading["available"]:
			line = "Tuner %s  --  device not available" % letter
			if history_width:
				line += "  |%s|" % (" " * history_width)
			lines.append(line)
			continue

		sig = reading["signal"]
		snr = reading["snr"]
		sig_pct = (sig / 65535.0 * 100.0) if sig is not None else 0.0
		snr_db = _snr_raw_to_db(snr, slot)
		lock_tag = "LOCK" if reading["locked"] else "...."

		snr_text = ("%5.1fdB" % snr_db) if snr_db is not None else "  --dB"
		line = (
			"Tuner %s  %s  S:%3d%% [%s] Q:%s"
			% (letter, lock_tag, sig_pct, _bar(sig), snr_text)
		)
		if history_width:
			line += "  |%s|" % monitor.sparkline(slot, history_width)
		lines.append(line)

	return "\n".join(lines)


def reading_display_values(slot, reading):
	"""
	Distills a raw reading into what should actually be DISPLAYED:
	  (sig_pct or None, effective_lock, snr_db or None, raw_lock)

	effective_lock gates FE_HAS_LOCK on a valid Q reading. Rationale:
	the AVL62X1 (SF8008 Ku tuner) blob driver LATCHES the last status in
	FE_READ_STATUS - between blindscan steps the demod sits idle (the
	blindscan binary drives it through its own path), so a genuine lock
	at the end of one step stays asserted through most of the next step
	until the demod state machine actually updates. Observed on-air as
	"LOCK ... Q: --dB" for tens of seconds. The AVL6261 C-band driver
	clears status on retune, which is why Tuner A never shows it. A real
	tracking lock always yields a plausible SNR (the 0-30dB guard in
	snr_raw_to_db), so requiring both kills the stale case while costing
	at most one 400ms tick on a genuine lock whose first SNR read is 0.
	raw_lock is returned too so the UI can distinguish "verified lock"
	from "lock bit set but unverified (likely stale)".
	"""
	if reading is None or not reading["available"]:
		return None, False, None, False
	pct = signal_raw_to_pct(reading["signal"])
	# prefer a direct-dB CNR from the v5 property interface (Edision);
	# otherwise run the raw value through the per-box calibration
	snr_db = reading.get("snr_db")
	if snr_db is None:
		snr_db = snr_raw_to_db(reading["snr"], slot)
	raw_lock = bool(reading["locked"])
	# require a POSITIVE Q: idle demods commonly report raw SNR 0, and no
	# genuine tracking lock sits at 0.0dB, so 0/None both mean "unverified"
	return pct, raw_lock and snr_db is not None and snr_db > 0, snr_db, raw_lock


def format_reading_text(slot, reading):
	"""
	Single fixed-width status line for ONE tuner from an already-taken
	reading (does NOT read the frontend itself - the caller reads once
	per tick and feeds both this and the graph, avoiding double ioctls):

	  Tuner A  LOCK  S: 78% [#######---] Q: 12.4dB

	Lock tag: "LOCK" = verified (lock bit + valid Q), "lock" = lock bit
	set but no valid Q (stale latch, see reading_display_values),
	"...." = no lock.

	Rationale for single-tuner display: with both tuners shown, a second
	tuner sitting locked on a live channel produces readings that look
	like crossover/bleed from the tuner actually being blindscanned.
	Showing only the selected tuner removes that ambiguity.
	"""
	letter = _TUNER_LETTERS[slot] if slot < len(_TUNER_LETTERS) else str(slot)
	if reading is None or not reading["available"]:
		return "Tuner %s  --  device not available" % letter
	pct, locked, snr_db, raw_lock = reading_display_values(slot, reading)
	lock_tag = "LOCK" if locked else ("lock" if raw_lock else "....")
	snr_text = ("%5.1fdB" % snr_db) if snr_db is not None else "  --dB"
	return "Tuner %s  %s  S:%3d%% [%s] Q:%s" % (
		letter, lock_tag, pct or 0.0, _bar(reading["signal"]), snr_text)


def format_single_tuner_text(monitor, slot):
	"""Convenience wrapper: read the slot once and format it."""
	if slot is None or slot not in monitor.slots():
		return _("Selected tuner not available.")
	return format_reading_text(slot, monitor.read(slot))


# ---------------------------------------------------------------------------
# Sweep-style signal graph (oscilloscope / "hospital monitor" behaviour).
#
# Drawn onto an enigma2 CanvasSource (skin: <widget source="..."
# render="Canvas" .../>). Each poll tick paints ONE new column at the
# current x cursor, erases a small gap ahead of it, then advances; when
# the cursor reaches the right edge it wraps back to x=0 and overwrites
# the oldest data. Only ~3 fill rects per tick, so it costs essentially
# nothing even on the SF8008.
#
# Colors intentionally match the TNAP skin palette:
#   green  = FE_HAS_LOCK set        amber = RF present, no lock
#   red    = device unreadable      cyan tick = SNR (dB) overlay
# The lock coloring gives the user the exact same red/green semantics as
# the dish_move_signal_traces.png captures, live on the box.
# ---------------------------------------------------------------------------

GRAPH_COL_BG = 0x00000000
GRAPH_COL_GRID = 0x00303030
GRAPH_COL_CURSOR = 0x00f0f0f0
GRAPH_COL_LOCKED = 0x0056c856
GRAPH_COL_UNLOCKED = 0x00ffc000
GRAPH_COL_DEAD = 0x00ff4a3c
GRAPH_COL_SNR = 0x0000c8ff
GRAPH_SNR_FULL_SCALE_DB = 20.0


class SweepSignalGraph(object):
	"""
	width/height MUST match the Canvas widget's size= in the skin (the
	renderer clips anything outside, but the wrap point would be wrong).

	column_width: pixels advanced per sample. At the 400ms poll tick,
	3px on a 590px canvas = one full sweep roughly every 79s; the driver
	only refreshes S ~1-2x/sec anyway, so finer resolution buys nothing.
	gap: erased pixels ahead of the cursor - the classic sweep gap that
	makes it obvious where "now" is.

	Samples are kept in a ring buffer (one entry per column), not just
	pixels, so the whole trace can be redrawn, exported and re-imported
	on another screen/geometry - see save/restore_sweep_history() below,
	which is what carries the trace across the progress-panel ->
	results-screen transition instead of abruptly wiping it.
	"""

	def __init__(self, canvas, width, height, column_width=3, gap=14):
		self.canvas = canvas
		self.width = width
		self.height = height
		self.column_width = column_width
		self.gap = gap
		self.n_cols = max(1, width // column_width)
		self.draw_width = self.n_cols * column_width  # wrap point (<= width)
		self.samples = [None] * self.n_cols  # ring: (pct, locked, snr_db) or None
		self.cursor = 0  # column index the NEXT sample lands in

	def _grid_ys(self):
		# 25/50/75% reference lines
		return [int(self.height * f) for f in (0.25, 0.5, 0.75)]

	def _clear_pixels(self, x, w):
		# background + grid redraw for a pixel span, wrapping at draw_width
		while w > 0:
			part = min(w, self.draw_width - x)
			self.canvas.fill(x, 0, part, self.height, GRAPH_COL_BG)
			for y in self._grid_ys():
				self.canvas.fill(x, y, part, 1, GRAPH_COL_GRID)
			w -= part
			x = 0

	def _draw_column(self, col):
		"""(Re)paint one column from its stored sample."""
		x = col * self.column_width
		cw = self.column_width
		self._clear_pixels(x, cw)
		s = self.samples[col]
		if s is None:
			return
		pct, locked, snr_db = s
		if pct is None:  # device unreadable at that tick
			self.canvas.fill(x, self.height - 2, cw, 2, GRAPH_COL_DEAD)
			return
		p = max(0.0, min(100.0, pct))
		bar_h = max(1, int(self.height * p / 100.0))
		color = GRAPH_COL_LOCKED if locked else GRAPH_COL_UNLOCKED
		self.canvas.fill(x, self.height - bar_h, cw, bar_h, color)
		if snr_db is not None:
			frac = min(snr_db, GRAPH_SNR_FULL_SCALE_DB) / GRAPH_SNR_FULL_SCALE_DB
			y = self.height - max(2, min(self.height - 1, int(self.height * frac)))
			self.canvas.fill(x, y, cw, 2, GRAPH_COL_SNR)

	def _draw_sweep_head(self):
		"""Erase the gap ahead of the cursor + draw the 'now' hairline."""
		cx = self.cursor * self.column_width
		self._clear_pixels(cx, self.gap)
		self.canvas.fill(cx, 0, 1, self.height, GRAPH_COL_CURSOR)

	def _flush(self):
		self.canvas.flush()
		try:
			# some CanvasSource variants only repaint on an explicit
			# changed(); harmless no-op on the ones that flush themselves
			self.canvas.changed((self.canvas.CHANGED_DEFAULT,))
		except Exception:
			pass

	def reset(self):
		"""Full wipe: clears stored samples AND the display."""
		if self.canvas is None:
			return
		self.samples = [None] * self.n_cols
		self.cursor = 0
		self.canvas.fill(0, 0, self.width, self.height, GRAPH_COL_BG)
		for y in self._grid_ys():
			self.canvas.fill(0, y, self.draw_width, 1, GRAPH_COL_GRID)
		self._flush()

	def redraw(self):
		"""Repaint the entire trace from the sample buffer."""
		if self.canvas is None:
			return
		self.canvas.fill(0, 0, self.width, self.height, GRAPH_COL_BG)
		for col in range(self.n_cols):
			self._draw_column(col)
		self._draw_sweep_head()
		self._flush()

	def add_sample(self, signal_pct, locked, snr_db=None, available=True):
		"""Plot one poll tick. signal_pct 0-100, or None (also when
		available=False) for a dead/unreadable device."""
		if self.canvas is None:
			return
		if not available:
			signal_pct = None
		self.samples[self.cursor] = (signal_pct, locked, snr_db)
		self._draw_column(self.cursor)
		self.cursor = (self.cursor + 1) % self.n_cols
		self._draw_sweep_head()
		self._flush()

	def get_history(self):
		"""Stored samples in chronological order (oldest first)."""
		ordered = self.samples[self.cursor:] + self.samples[:self.cursor]
		return [s for s in ordered if s is not None]

	def load_history(self, samples):
		"""Replace the buffer with `samples` (chronological, possibly from
		a graph with a different column count - only the newest n_cols are
		kept) and repaint. Cursor ends up just past the newest sample, so
		the sweep continues exactly where the previous screen left off."""
		self.samples = [None] * self.n_cols
		samples = list(samples)[-self.n_cols:]
		for i, s in enumerate(samples):
			self.samples[i] = s
		self.cursor = len(samples) % self.n_cols
		self.redraw()


# ---------------------------------------------------------------------------
# Cross-screen sweep persistence.
#
# The blindscan UI is three different Screen instances (config menu ->
# progress panel -> results screen), each with its own SweepSignalGraph.
# Without this, every transition wiped the accumulated trace - most
# annoyingly at end-of-scan, when the results screen replaced a full
# band sweep with an empty graph. Screens save their trace here (keyed
# by NIM slot) every tick and load it back on open, so the trace flows
# seamlessly across screens - and across tuner switches in the config
# menu, since each slot keeps its own history. Module-level = survives
# for the enigma2 session; the age cap stops an hours-old trace from
# being presented as current if the plugin is reopened much later.
# ---------------------------------------------------------------------------

_SWEEP_HISTORY = {}  # slot -> (unix timestamp of last save, [samples])
_SWEEP_HISTORY_MAX_AGE = 300.0  # seconds


def save_sweep_history(slot, graph):
	"""Snapshot a graph's trace for `slot`. Cheap enough to call every
	poll tick (a few hundred tuple refs), which sidesteps any screen
	open/close ordering questions."""
	if slot is None or graph is None:
		return
	_SWEEP_HISTORY[slot] = (time.time(), graph.get_history())


def restore_sweep_history(slot, graph):
	"""Load `slot`'s persisted trace into `graph` (repaints), or plain
	reset() when nothing fresh is stored. Returns True if restored."""
	entry = _SWEEP_HISTORY.get(slot) if slot is not None else None
	if entry:
		ts, samples = entry
		if samples and (time.time() - ts) <= _SWEEP_HISTORY_MAX_AGE:
			graph.load_history(samples)
			return True
	graph.reset()
	return False
