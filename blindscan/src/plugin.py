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
