from __future__ import print_function
from . import _
from enigma import eDVBFrontendParametersSatellite
from Components.config import config, ConfigBoolean, ConfigInteger, ConfigSelection, ConfigSubsection, ConfigYesNo
from Tools.Directories import fileExists


BOX_MODEL = "all"
BOX_NAME = "none"
if fileExists("/proc/stb/info/vumodel") and not fileExists("/proc/stb/info/hwmodel") and not fileExists("/proc/stb/info/boxtype"):
	try:
		l = open("/proc/stb/info/vumodel")
		model = l.read().strip()
		l.close()
		BOX_NAME = str(model.lower())
		BOX_MODEL = "vuplus"
	except:
		pass
elif fileExists("/proc/stb/info/boxtype") and not fileExists("/proc/stb/info/hwmodel") and not fileExists("/proc/stb/info/gbmodel"):
	try:
		p = 0
		nimfile = open("/proc/bus/nim_sockets")
		for line in nimfile:
			line = line.strip()
			if line.endswith("AVL62X1"):
				p = 1
		l = open("/proc/stb/info/boxtype")
		model = l.read().strip()
		l.close()
		BOX_NAME = str(model.lower())
		if BOX_NAME.startswith("et"):
			BOX_MODEL = "xtrend"
		elif BOX_NAME.startswith("os"):
			BOX_MODEL = "edision"
		elif BOX_NAME.startswith("sf"):
			BOX_MODEL = "octagon"
		if p == 1 and BOX_NAME.startswith("sf"):
			BOX_NAME = "sf8008-Supreme"
		nimfile.close()
	except:
		pass
elif fileExists("/proc/stb/info/model") and not fileExists("/proc/stb/info/hwmodel") and not fileExists("/proc/stb/info/gbmodel"):
	try:
		l = open("/proc/stb/info/model")
		model = l.read().strip()
		l.close()
		BOX_NAME = str(model.lower())
		if BOX_NAME.startswith('dm'):
			BOX_MODEL = "dreambox"
	except:
		pass
elif fileExists("/proc/stb/info/gbmodel"):
	try:
		l = open("/proc/stb/info/gbmodel")
		model = l.read().strip()
		l.close()
		BOX_NAME = str(model.lower())
		if BOX_NAME in ("gbquad4k", "gbue4k", "gbtrio4k"):
			BOX_MODEL = "gigablue"
	except:
		pass
elif fileExists("/proc/stb/info/hwmodel"):
	try:
		l = open("/proc/stb/info/hwmodel")
		model = l.read().strip()
		l.close()
		BOX_NAME = str(model.lower())
		if BOX_NAME in ("lunix4k", "dual"):
			BOX_MODEL = "qviart"
	except:
		pass
elif fileExists("/proc/stb/info/boxtype"):
	try:
		l = open("/proc/stb/info/boxtype")
		model = l.read().strip()
		l.close()
		BOX_NAME = str(model.lower())
		if BOX_NAME == "ustym4kpro":
			BOX_MODEL = "uclan"

	except:
		pass


# root2gold based on https://github.com/DigitalDevices/dddvb/blob/master/apps/pls.c

def check_tnap_image():
	try:
		with open('/etc/issue', 'r') as f:
			issue_content = f.read().strip()
			if 'TNAP' not in issue_content:
				return False
			return True
	except:
		return False

def root2gold(root):
	if root < 0 or root > 0x3ffff:
		return 0
	g = 0
	x = 1
	while g < 0x3ffff:
		if root == x:
			return g
		x = (((x ^ (x >> 7)) & 1) << 17) | (x >> 1)
		g += 1
	return 0

# helper function for initializing mis/pls properties


def getMisPlsValue(d, idx, defaultValue):
	try:
		return int(d[idx])
	except:
		return defaultValue

#used for blindscan-s2


def getAdapterFrontend(frontend, description):
	for adapter in range(1, 5):
		try:
			product = open("/sys/class/dvb/dvb%d.frontend0/device/product" % adapter).read()
			if description in product:
				return " -a %d" % adapter
		except:
			break
	return " -f %d" % frontend


XML_BLINDSCAN_DIR = "/tmp"
XML_FILE = None
BLINDSCAN_STEP_SETTLE_MS = 200

# _supportNimType is only used by vuplus hardware
_supportNimType = {'AVL1208': '', 'AVL6222': '6222_', 'AVL6211': '6211_', 'BCM7356': 'bcm7346_', 'SI2166': 'si2166_'}

# For STBs that support PnP DVB-S/S2 tuner models, e.g. VU+Solo 4K,VU+Ultimo 4K,Gigablue UE/Quad 4K
_unsupportedNims = ("Vuplus DVB-S NIM(7376 FBC)", "Vuplus DVB-S NIM(45308X FBC)", "DVB-S2 NIM(45308 FBC)", "DVB-S2 NIM(45208 FBC)", "DVB-S2X NIM(45308X FBC)", "DVB-S2 NIM(45308 FBC)") # format = nim.description from nimmanager

# blindscan-s2 supported tuners
_blindscans2Nims = ('TBS-5925', 'DVBS2BOX', 'M88DS3103')

defaults = {"search_type": "transponders",
	"user_defined_lnb_inversion": False,
	"step_mhz_tbs5925": 10,
	"polarization": str(eDVBFrontendParametersSatellite.Polarisation_CircularRight + 1), # "vertical and horizontal"
	"start_symbol": 1,
	"stop_symbol": 60,
	"clearallservices": "no",
	"onlyFTA": False,
	"lamedb": False,
	"dont_scan_known_tps": False,
	"disable_sync_with_known_tps": True,
	"disable_remove_duplicate_tps": True,
	"blindscan_user_defined_lnb_start_frequency": 11700,
	"scan_mis": True,
	"verify_orbital_position": True,
	"filter_off_adjacent_satellites": "0",
	"Ku_band_start_frequency": 10700,
	"Ku_band_stop_frequency": 12750,
	"C_band_start_frequency": 3400,
	"C_band_stop_frequency": 4200,
	"C_band_5750_start_frequency": 3625,
	"C_band_5750_stop_frequency": 4800,
	"C_band_bandstack_start_frequency": 3625,
	"C_band_bandstack_stop_frequency": 4800,
	"user_defined_lnb_start_freq": 0,
	"user_defined_lnb_stop_freq": 0,
	"user_defined_lnb_inverted_start_freq": 0,
	"user_defined_lnb_inverted_stop_freq": 0}

config.blindscan = ConfigSubsection()
config.blindscan.search_type = ConfigSelection(default=defaults["search_type"], choices=[
	("services", _("scan for channels")),
	("transponders", _("scan for transponders"))])
config.blindscan.user_defined_lnb_inversion = ConfigBoolean(default=defaults["user_defined_lnb_inversion"], descriptions={False: _("normal"), True: _("inverted")})
config.blindscan.step_mhz_tbs5925 = ConfigInteger(default=defaults["step_mhz_tbs5925"], limits=(1, 20))
config.blindscan.polarization = ConfigSelection(default=defaults["polarization"], choices=[
	(str(eDVBFrontendParametersSatellite.Polarisation_CircularRight + 1), _("vertical and horizontal")),
	(str(eDVBFrontendParametersSatellite.Polarisation_Vertical), _("vertical")),
	(str(eDVBFrontendParametersSatellite.Polarisation_Horizontal), _("horizontal")),
	(str(eDVBFrontendParametersSatellite.Polarisation_CircularRight + 2), _("circular right and circular left")),
	(str(eDVBFrontendParametersSatellite.Polarisation_CircularRight), _("circular right")),
	(str(eDVBFrontendParametersSatellite.Polarisation_CircularLeft), _("circular left"))])
config.blindscan.start_symbol = ConfigInteger(default=defaults["start_symbol"], limits=(0, 59))
config.blindscan.stop_symbol = ConfigInteger(default=defaults["stop_symbol"], limits=(2, 60))
config.blindscan.clearallservices = ConfigSelection(default=defaults["clearallservices"], choices=[("no", _("no")), ("yes", _("yes")), ("yes_hold_feeds", _("yes (keep feeds)"))])
config.blindscan.onlyFTA = ConfigYesNo(default=defaults["onlyFTA"])
config.blindscan.lamedb = ConfigYesNo(default=defaults["lamedb"])
config.blindscan.dont_scan_known_tps = ConfigYesNo(default=defaults["dont_scan_known_tps"])
config.blindscan.disable_sync_with_known_tps = ConfigYesNo(default=defaults["disable_sync_with_known_tps"])
config.blindscan.disable_remove_duplicate_tps = ConfigYesNo(default=defaults["disable_remove_duplicate_tps"])
config.blindscan.filter_off_adjacent_satellites = ConfigSelection(default=defaults["filter_off_adjacent_satellites"], choices=[
	("0", _("no")),
	("1", _("up to 1 degree")),
	("2", _("up to 2 degrees")),
	("3", _("up to 3 degrees"))])
config.blindscan.scan_mis = ConfigYesNo(default=defaults["scan_mis"])
config.blindscan.verify_orbital_position = ConfigYesNo(default=defaults["verify_orbital_position"])
config.blindscan.Ku_band_start_frequency = ConfigInteger(default=defaults["Ku_band_start_frequency"], limits=(10000, 13000))
config.blindscan.Ku_band_stop_frequency = ConfigInteger(default=defaults["Ku_band_stop_frequency"], limits=(10001, 13001))
config.blindscan.C_band_start_frequency = ConfigInteger(default=defaults["C_band_start_frequency"], limits=(3000, 4200))
config.blindscan.C_band_stop_frequency = ConfigInteger(default=defaults["C_band_stop_frequency"], limits=(3001, 4201))
config.blindscan.C_band_5750_start_frequency = ConfigInteger(default=defaults["C_band_5750_start_frequency"], limits=(3600, 4820))
config.blindscan.C_band_5750_stop_frequency = ConfigInteger(default=defaults["C_band_5750_stop_frequency"], limits=(3601, 4821))
config.blindscan.C_band_bandstack_start_frequency = ConfigInteger(default=defaults["C_band_bandstack_start_frequency"], limits=(3600, 4820))
config.blindscan.C_band_bandstack_stop_frequency = ConfigInteger(default=defaults["C_band_bandstack_stop_frequency"], limits=(3601, 4821))
config.blindscan.user_defined_lnb_start_freq = ConfigInteger(default=defaults["user_defined_lnb_start_freq"], limits=(0, 30000))
config.blindscan.user_defined_lnb_stop_freq = ConfigInteger(default=defaults["user_defined_lnb_stop_freq"], limits=(0, 30000))
config.blindscan.user_defined_lnb_inverted_start_freq = ConfigInteger(default=defaults["user_defined_lnb_inverted_start_freq"], limits=(0, 30000))
config.blindscan.user_defined_lnb_inverted_stop_freq = ConfigInteger(default=defaults["user_defined_lnb_inverted_stop_freq"], limits=(0, 30000))

# Per-NIM last-used orbital position — persisted so that when the plugin is opened
# on one tuner, the other tuner's satellite dropdown defaults to what was last watched
# on that tuner rather than falling back to a garbage or first-entry default.
config.blindscan.last_nim_orbpos = ConfigSubsection()
for _nim_idx in range(4):  # supports up to 4 NIM slots
	setattr(config.blindscan.last_nim_orbpos, "nim%d" % _nim_idx,
	        ConfigInteger(default=0, limits=(0, 3600)))

try:
	Lastrotorposition = config.misc.lastrotorposition
except Exception:
	Lastrotorposition = None
