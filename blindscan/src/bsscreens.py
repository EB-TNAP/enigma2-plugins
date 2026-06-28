from __future__ import print_function
from . import _
from Components.ActionMap import ActionMap
from Components.config import ConfigBoolean, getConfigListEntry
from Components.ConfigList import ConfigListScreen
from Components.Label import Label
from Components.ScrollLabel import ScrollLabel
from Components.Sources.StaticText import StaticText
from Screens.Screen import Screen
from .bsconfig import BOX_NAME


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
