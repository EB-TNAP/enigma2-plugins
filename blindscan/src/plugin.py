from __future__ import print_function
from . import _
from enigma import eTimer
from Components.ActionMap import ActionMap
from Components.ConfigList import ConfigListScreen
from Components.Label import Label
from Components.NimManager import nimmanager
from Components.Sources.FrontendStatus import FrontendStatus
from Plugins.Plugin import PluginDescriptor
from Screens.ChoiceBox import ChoiceBox
from Screens.MessageBox import MessageBox
from Screens.Screen import Screen
from Tools.BoundFunction import boundFunction
from .filters import TransponderFiltering
from time import time
import os
from . import bsconfig
from .bsconfig import BOX_MODEL, BOX_NAME, check_tnap_image, _unsupportedNims
from .bsresults import BlindscanResultsMixin
from .bsengine import BlindscanEngineMixin
from .bsui import BlindscanUIMixin
from .bsverify import BlindscanVerifyMixin


class Blindscan(BlindscanUIMixin, BlindscanEngineMixin, BlindscanVerifyMixin, BlindscanResultsMixin, ConfigListScreen, Screen, TransponderFiltering):
	# Self-contained FHD layout (skin-independent-screens.md pattern):
	# wfNoBorder + own opaque background, Title drawn from the Title source,
	# clock/date from global.CurrentTime, all colors inline hex, no <panel>
	# includes, no external pixmaps (color strips are plain eLabels).
	skin = """
	<screen name="BlindscanTNAP" position="0,0" size="1920,1080" title="Blind scan" flags="wfNoBorder" backgroundColor="#00000000" resolution="1920,1080">
		<eLabel position="0,0" size="1920,1080" backgroundColor="#00000000" zPosition="-2"/>

		<!-- outer frame: encloses header + body as one panel -->
		<eLabel position="20,20"   size="1880,2" backgroundColor="#00f0f0f0"/>
		<eLabel position="20,948"  size="1880,2" backgroundColor="#00f0f0f0"/>
		<eLabel position="20,20"   size="2,930"  backgroundColor="#00f0f0f0"/>
		<eLabel position="1898,20" size="2,930"  backgroundColor="#00f0f0f0"/>

		<!-- header band: title + rotor status left, LOCK + clock/date right -->
		<widget source="Title" render="Label" position="45,34" size="1330,54" font="Regular;40" transparent="1" valign="center" halign="left" noWrap="1" foregroundColor="#00f0f0f0"/>
		<widget name="rotorstatus" position="45,94" size="900,36" font="Regular;28" foregroundColor="#00ffc000" transparent="1"/>
		<widget text="LOCK" source="Frontend" render="FixedLabel" position="1400,36" size="130,50" font="Regular;38" valign="center" halign="center" foregroundColor="#0056c856" transparent="1" zPosition="2">
			<convert type="FrontendInfo">LOCK</convert>
			<convert type="ConditionalShowHide"/>
		</widget>
		<widget source="global.CurrentTime" render="Label" position="1560,30" size="310,52" font="Regular;44" halign="right" transparent="1" foregroundColor="#00f0f0f0">
			<convert type="ClockToText">Format:%H:%M</convert>
		</widget>
		<widget source="global.CurrentTime" render="Label" position="1300,88" size="570,34" font="Regular;26" halign="right" transparent="1" foregroundColor="#00909090">
			<convert type="ClockToText">Format:%A %e %B %Y</convert>
		</widget>

		<!-- header separator (dimmer than the frame) -->
		<eLabel position="22,140" size="1876,1" backgroundColor="#00808080"/>

		<!-- body: config list left, contextual help right -->
		<widget name="config" position="45,158" size="1180,660" font="Regular;28" itemHeight="44" scrollbarMode="showOnDemand" transparent="0" backgroundColor="#00000000" foregroundColor="#00f0f0f0" backgroundColorSelected="#06303240" foregroundColorSelected="#00fcc000"/>

		<!-- vertical divider: body region only, dimmer than the frame -->
		<eLabel position="1255,158" size="2,660" backgroundColor="#00808080"/>

		<widget name="description" position="1285,158" size="590,660" font="Regular;28" foregroundColor="#00ffc000" transparent="1"/>

		<!-- footer hairline above the introduction line -->
		<eLabel position="22,842" size="1876,1" backgroundColor="#00808080"/>
		<widget name="introduction" position="45,866" size="1830,50" font="Regular;32" foregroundColor="#0056c856" halign="center" valign="center" transparent="1"/>

		<!-- color key bar: self-contained eLabel strips, no pixmaps -->
		<widget name="key_red"    position="40,968"  size="220,56" font="Regular;30" halign="center" valign="center" foregroundColor="#00f0f0f0" transparent="1"/>
		<widget name="key_green"  position="300,968" size="220,56" font="Regular;30" halign="center" valign="center" foregroundColor="#00f0f0f0" transparent="1"/>
		<widget name="key_yellow" position="560,968" size="220,56" font="Regular;30" halign="center" valign="center" foregroundColor="#00f0f0f0" transparent="1"/>
		<widget name="key_blue"   position="820,968" size="220,56" font="Regular;30" halign="center" valign="center" foregroundColor="#00f0f0f0" transparent="1"/>
		<eLabel position="40,1030"  size="220,4" backgroundColor="#00ff4a3c"/>
		<eLabel position="300,1030" size="220,4" backgroundColor="#0056c856"/>
		<eLabel position="560,1030" size="220,4" backgroundColor="#00F9C731"/>
		<eLabel position="820,1030" size="220,4" backgroundColor="#00879ce1"/>
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
		self.skinName = ["BlindscanTNAP"]
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
