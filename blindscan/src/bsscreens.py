from __future__ import print_function
from . import _
from enigma import eTimer
from Components.ActionMap import ActionMap
from Components.config import ConfigBoolean, getConfigListEntry
from Components.ConfigList import ConfigListScreen
from Components.Label import Label
from Components.ScrollLabel import ScrollLabel
from Components.Sources.CanvasSource import CanvasSource
from Components.Sources.StaticText import StaticText
from Screens.Screen import Screen
from .bsconfig import BOX_NAME
from .bssignalmonitor import DishSignalMonitor, SweepSignalGraph, format_reading_text, reading_display_values, save_sweep_history, restore_sweep_history


class BlindscanState(ConfigListScreen, Screen):
	skin = """
	<screen position="center,center" size="1728,972" title="Satellite Blindscan" backgroundColor="black" flags="wfNoBorder">
		<eLabel position="0,0" size="1728,972" backgroundColor="black" zPosition="-1"/>

		<!-- outer frame: encloses header + body as one panel -->
		<eLabel position="20,22"   size="1688,2"  backgroundColor="white"/>
		<eLabel position="20,852"  size="1688,2"  backgroundColor="white"/>
		<eLabel position="20,22"   size="3,833"   backgroundColor="white"/>
		<eLabel position="1705,22" size="3,833"   backgroundColor="white"/>

		<!-- status / summary text (header band) -->
		<widget name="progress" position="51,37" size="1625,173" font="Regular;24" transparent="1"/>

		<!-- box / model identifier (top-right of header) -->
		<widget source="boxname" render="Label" position="1145,32" size="540,39" font="Regular;28" halign="right" foregroundColor="#00909090" transparent="1"/>

		<!-- header separator (dimmer than the frame) -->
		<eLabel position="23,221" size="1682,1" backgroundColor="#00808080"/>

		<!-- vertical divider: body region only, dimmer than the frame -->
		<eLabel position="900,224" size="3,629" backgroundColor="#00808080"/>

		<!-- left column: config when finished, found while scanning -->
		<widget name="config" position="51,234" size="830,605" font="Regular;22" />
		<widget name="found"  position="51,234" size="830,605" font="Regular;20" foregroundColor="#00ffc000" transparent="1"/>

		<!-- right column: status / instructions -->
		<widget name="post_action" position="925,234" size="760,250" font="Regular;22" halign="center" transparent="1"/>

		<!-- live signal monitor for the tuner the blindscan is running on:
		     one raw S/Q status line plus a sweeping left-to-right graph
		     (green=locked, amber=no lock, cyan tick=Q dB) that wraps back
		     to the left edge, updating continuously (including pre-lock)
		     so dish movement is visible during/after scanning. -->
		<widget name="dishmonitor" position="925,540" size="760,28" font="Console;20" foregroundColor="#0056c856" transparent="1"/>
		<widget source="signalgraph" render="Canvas" position="925,578" size="760,254" transparent="1"/>

		<!-- footer hairline above the key bar -->
		<eLabel position="20,865" size="1688,1" backgroundColor="#00808080"/>

		<!-- color key bar (unchanged) -->
		<ePixmap pixmap="/usr/lib/enigma2/python/Plugins/SystemPlugins/Blindscan/images/red.png"    position="14,940"  size="189,4" alphatest="on"/>
		<ePixmap pixmap="/usr/lib/enigma2/python/Plugins/SystemPlugins/Blindscan/images/green.png"  position="230,940" size="189,4" alphatest="on"/>
		<ePixmap pixmap="/usr/lib/enigma2/python/Plugins/SystemPlugins/Blindscan/images/yellow.png" position="446,940" size="189,4" alphatest="on"/>
		<ePixmap pixmap="/usr/lib/enigma2/python/Plugins/SystemPlugins/Blindscan/images/blue.png"   position="662,940" size="189,4" alphatest="on"/>
		<widget source="key_red"    render="Label" position="14,875"  size="189,65" font="Regular;28" halign="center" transparent="1"/>
		<widget source="key_green"  render="Label" position="230,875" size="189,65" font="Regular;28" halign="center" transparent="1"/>
		<widget source="key_yellow" render="Label" position="446,875" size="189,65" font="Regular;28" halign="center" transparent="1"/>
		<widget source="key_blue"   render="Label" position="662,875" size="189,65" font="Regular;28" halign="center" transparent="1"/>
	</screen>
	"""


	def __init__(self, session, progress, post_action, tp_list, finished=False, tuner_slot=None):
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

		# Live raw S/Q readout + sweep graph for the tuner the blindscan
		# was configured for (tuner_slot, passed by the caller from
		# scan_nims). Single-tuner display avoids apparent crossover
		# readings when the other tuner sits locked on a live channel.
		# Purely observational - it opens its own read-only fd and never
		# touches tuning, so it's safe alongside (or after) the scan. Note
		# it's expected to show S:0%/dead once the scan finishes and the
		# engine releases the frontend, since that's what cuts LNB power.
		self.dishMonitor = DishSignalMonitor()
		if tuner_slot is None:
			slots = self.dishMonitor.slots()
			tuner_slot = slots[0] if slots else None
		self.tunerSlot = tuner_slot
		self["dishmonitor"] = Label("")
		self["signalgraph"] = CanvasSource()
		# width/height must match the Canvas widget size= in the skin
		self.signalGraph = SweepSignalGraph(self["signalgraph"], 760, 254)
		# resume the trace accumulated by the previous screen (config menu
		# or progress panel) instead of wiping it - notably this is what
		# stops the end-of-scan results screen from abruptly resetting a
		# full band sweep back to an empty graph
		self.onLayoutFinish.append(self._graphRestoreInitial)
		self.dishMonitorTimer = eTimer()
		self.dishMonitorTimer.callback.append(self.updateDishMonitor)
		self.dishMonitorTimer.start(400, False)
		self.onClose.append(self.stopDishMonitor)

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

	def _graphRestoreInitial(self):
		restore_sweep_history(self.tunerSlot, self.signalGraph)

	def updateDishMonitor(self):
		try:
			slot = self.tunerSlot
			if slot is None or slot not in self.dishMonitor.slots():
				self["dishmonitor"].setText(_("Selected tuner not available."))
				self.signalGraph.add_sample(None, False, available=False)
				return
			# read ONCE per tick, feed both the text line and the graph
			reading = self.dishMonitor.read(slot)
			self["dishmonitor"].setText(format_reading_text(slot, reading))
			pct, locked, snr_db, _raw_lock = reading_display_values(slot, reading)
			self.signalGraph.add_sample(pct, locked, snr_db)
			# snapshot every tick so the next screen (results panel, or the
			# config menu on return) resumes this trace seamlessly
			save_sweep_history(slot, self.signalGraph)
		except Exception as e:
			print("[BlindscanState][updateDishMonitor] error:", e)

	def stopDishMonitor(self):
		self.dishMonitorTimer.stop()
		save_sweep_history(self.tunerSlot, self.signalGraph)
		self.dishMonitor.close()
