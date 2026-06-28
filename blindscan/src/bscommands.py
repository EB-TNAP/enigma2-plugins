from __future__ import print_function
from .bsconfig import (BOX_MODEL, BOX_NAME, _blindscans2Nims, getAdapterFrontend)
import os


class _HardwareNotSupported(Exception):
    pass


class _ToolNotFound(Exception):
    def __init__(self, tool, abort=True):
        self.tool = tool
        self.abort = abort  # False: show error but do not abort prepareScanData


def build_scan_command(tunername, bin_name,
                       temp_start_int_freq, temp_end_int_freq,
                       pol, band, tab_pol, tab_hilow,
                       feid, nim_socket,
                       is_c_band_scan, c_band_lo_freq, universal_lo_freq,
                       orb, start_symbol, stop_symbol, step_mhz_tbs5925):
    """
    Build the blindscan binary command for the current hardware.

    Returns (cmd, async_cmd, adjust_freq_override):
      cmd              -- command string for eConsoleAppContainer sync path
      async_cmd        -- command string for blindscan-s2 deferred (async) path;
                          non-empty only for blindscan-s2; when set, cmd is "".
      adjust_freq_override -- None (keep existing) or False (set self.adjust_freq=False)

    Raises _HardwareNotSupported when the box has no support at all.
    Raises _ToolNotFound(tool, abort) when the binary is missing;
      abort=False for blindscan-s2 (original code falls through without returning).
    """
    cmd = ""

    if tunername in _blindscans2Nims:
        tools = "/usr/bin/blindscan-s2"
        if os.path.exists(tools):
            if tunername == "TBS-5925":
                cmd = "blindscan-s2 -b -s %d -e %d -t %d" % (temp_start_int_freq, temp_end_int_freq, step_mhz_tbs5925)
            else:
                cmd = "blindscan-s2 -b -s %d -e %d" % (temp_start_int_freq, temp_end_int_freq)
            cmd += getAdapterFrontend(feid, tunername)
            if pol == "horizontal":
                cmd += " -H"
            elif pol == "vertical":
                cmd += " -V"
            if is_c_band_scan:
                cmd += " -l %d" % c_band_lo_freq
            elif tab_hilow[band]:
                cmd += " -l %d -2" % universal_lo_freq["high"]
            else:
                cmd += " -l %d" % universal_lo_freq["low"]
            return ("", cmd, None)  # async path: caller sets self.cmd and starts bsTimer
        else:
            raise _ToolNotFound(tools, abort=False)  # show error but don't abort
    elif BOX_NAME in ("mbtwinplus", "mbmicro", "mbmicrov2"):
        tools = "/usr/bin/ceryon_blindscan"
        if os.path.exists(tools):
            cmd = "ceryon_blindscan %d %d %d %d %d %d %d %d" % (temp_start_int_freq, temp_end_int_freq, start_symbol, stop_symbol, tab_pol[pol], tab_hilow[band], feid, nim_socket)
            cmd += " %d" % is_c_band_scan
        else:
            raise _ToolNotFound(tools)
    elif BOX_MODEL == "vuplus":
        if BOX_NAME in ("uno", "duo2", "solo2", "solose", "ultimo", "solo4k", "ultimo4k", "zero4k"):
            tools = "/usr/bin/%s" % bin_name
            if os.path.exists(tools):
                cmd = "%s %d %d %d %d %d %d %d %d" % (bin_name, temp_start_int_freq, temp_end_int_freq, start_symbol, stop_symbol, tab_pol[pol], tab_hilow[band], feid, nim_socket)
            else:
                raise _ToolNotFound(tools)
        else:
            raise _HardwareNotSupported()
    elif BOX_MODEL.startswith("xtrend"):
        if BOX_NAME.startswith("et9") or BOX_NAME.startswith("et6") or BOX_NAME.startswith("et5"):
            tools = "/usr/bin/avl_xtrend_blindscan"
            if os.path.exists(tools):
                cmd = "avl_xtrend_blindscan %d %d %d %d %d %d %d %d" % (temp_start_int_freq, temp_end_int_freq, start_symbol, stop_symbol, tab_pol[pol], tab_hilow[band], feid, nim_socket)
            else:
                raise _ToolNotFound(tools)
        else:
            raise _HardwareNotSupported()
    elif BOX_MODEL.startswith("edision"):
        tools = "/usr/bin/blindscan"
        if os.path.exists(tools):
            cmd = "blindscan --start=%d --stop=%d --min=%d --max=%d --slot=%d --i2c=%d" % (temp_start_int_freq, temp_end_int_freq, start_symbol, stop_symbol, feid, nim_socket)
            if tab_pol[pol]:
                cmd += " --vertical"
            if is_c_band_scan:
                cmd += " --cband"
            elif tab_hilow[band]:
                cmd += " --high"
        else:
            raise _ToolNotFound(tools)
    elif BOX_NAME == "lunix4k":
        tools = "/usr/bin/qviart_blindscan_72604"
        if os.path.exists(tools):
            cmd = "qviart_blindscan_72604 %d %d %d %d %d %d %d %d %d %d" % (temp_start_int_freq, temp_end_int_freq, start_symbol, stop_symbol, tab_pol[pol], tab_hilow[band], feid, nim_socket, is_c_band_scan, orb)
        else:
            raise _ToolNotFound(tools)
    elif BOX_NAME == "dual":
        tools = "/usr/bin/qviart_blindscan"
        if os.path.exists(tools):
            cmd = "qviart_blindscan %d %d %d %d %d %d %d %d %d %d" % (temp_start_int_freq, temp_end_int_freq, start_symbol, stop_symbol, tab_pol[pol], tab_hilow[band], feid, nim_socket, is_c_band_scan, orb)
        else:
            raise _ToolNotFound(tools)
    elif BOX_NAME.startswith("ustym"):
        tools = "/usr/bin/uclan-blindscan"
        if os.path.exists(tools):
            cmd = "uclan-blindscan %d %d %d %d %d %d %d %d %d %d" % (temp_start_int_freq, temp_end_int_freq, start_symbol, stop_symbol, tab_pol[pol], tab_hilow[band], feid, nim_socket, is_c_band_scan, orb)
            return (cmd, "", False)  # adjust_freq must be set to False
        else:
            raise _ToolNotFound(tools)
    elif BOX_NAME.startswith("sf8008"):
        tools = "/usr/bin/octagon-blindscan"
        if os.path.exists(tools):
            cmd = "octagon-blindscan %d %d %d %d %d %d %d %d %d 2100" % (temp_start_int_freq, temp_end_int_freq, start_symbol, stop_symbol, tab_pol[pol], tab_hilow[band], feid, nim_socket, is_c_band_scan)
        else:
            raise _ToolNotFound(tools)
    elif BOX_MODEL == "gigablue":
        tools = "/usr/bin/gigablue_blindscan"
        if os.path.exists(tools):
            cmd = "gigablue_blindscan %d %d %d %d %d %d %d %d" % (temp_start_int_freq, temp_end_int_freq, start_symbol, stop_symbol, tab_pol[pol], tab_hilow[band], feid, nim_socket)
            if BOX_NAME == "gbtrio4k":
                cmd += " %d" % is_c_band_scan
                cmd += " %d" % orb
                return (cmd, "", False)  # adjust_freq must be set to False
        else:
            raise _ToolNotFound(tools)
    else:
        raise _HardwareNotSupported()

    return (cmd, "", None)
