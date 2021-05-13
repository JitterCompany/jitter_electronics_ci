#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) 2020-2021 S de Wit
# Copyright (c) 2020-2021 Salvador E. Tropea
# Copyright (c) 2020-2021 Instituto Nacional de Tecnologïa Industrial
# Copyright (c) 2019 Jesse Vincent (@obra)
# Copyright (c) 2018-2019 Seppe Stas (@seppestas) (Productize SPRL)
# Based on ideas by: Scott Bezek (@scottbez1)
# License: Apache 2.0
# Project: KiAuto (formerly kicad-automation-scripts)
# Adapted from: https://github.com/obra/kicad-automation-scripts
"""
Various pcbnew operations

This program runs pcbnew and can:
1) Print PCB layers
2) Run the DRC
3) Export a 3D render image
The process is graphical and very delicated.
"""

import sys
import os
import argparse
import atexit
import re
import subprocess
import psutil
from time import (asctime, localtime, sleep)
import gettext
import json
import shutil

# Look for the 'kiauto' module from where the script is running
script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(script_dir))
# Utils import
# Log functionality first
from kiauto import log
log.set_domain(os.path.splitext(os.path.basename(__file__))[0])
logger = log.init()

from kiauto.file_util import (load_filters, wait_for_file_created_by_process, apply_filters, list_errors, list_warnings,
                              check_kicad_config_dir, restore_config, backup_config, check_lib_table, create_user_hotkeys,
                              check_input_file, memorize_project, restore_project, get_log_files)
from kiauto.misc import (REC_W, REC_H, __version__, NO_PCB, PCBNEW_CFG_PRESENT, WAIT_START, WRONG_LAYER_NAME,
                         WRONG_PCB_NAME, PCBNEW_ERROR, WRONG_ARGUMENTS, Config, KICAD_VERSION_5_99, USER_HOTKEYS_PRESENT,
                         CORRUPTED_PCB, __copyright__, __license__, TIME_OUT_MULT)
from kiauto.ui_automation import (PopenContext, xdotool, wait_not_focused, wait_for_window, recorded_xvfb,
                                  wait_point, text_replace, set_time_out_scale)

TITLE_CONFIRMATION = '^Confirmation$'
TITLE_ERROR = '^Error$'
TITLE_WARNING = '^Warning$'


def parse_drc(cfg):
    with open(cfg.output_file, 'rt') as f:
        lines = f.read().splitlines()
    drc_errors = None
    unconnected_pads = None
    in_errs = False
    in_wrns = False
    if cfg.kicad_version >= KICAD_VERSION_5_99:
        err_regex = re.compile(r'^\[(\S+)\]: (.*)')
    else:
        err_regex = re.compile(r'^ErrType\((\d+)\): (.*)')
    for line in lines:
        m = re.search(r'^\*\* Found ([0-9]+) DRC (errors|violations) \*\*$', line)
        if m:
            drc_errors = m.group(1)
            in_errs = True
            continue
        m = re.search(r'^\*\* Found ([0-9]+) unconnected pads \*\*$', line)
        if m:
            unconnected_pads = m.group(1)
            in_errs = False
            in_wrns = True
            continue
        m = re.search(r'^\*\* End of Report \*\*$', line)
        if m:
            break
        if in_errs:
            m = err_regex.search(line)
            if m:
                cfg.errs.append('({}) {}'.format(m.group(1), m.group(2)))
                continue
            if len(line) > 4 and len(cfg.errs) > 0:
                cfg.errs.append(cfg.errs.pop()+'\n'+line)
                continue
        if in_wrns:
            m = err_regex.search(line)
            if m:
                cfg.wrns.append('({}) {}'.format(m.group(1), m.group(2)))
                continue
            if len(line) > 4 and len(cfg.wrns) > 0:
                cfg.wrns.append(cfg.wrns.pop()+'\n'+line)
                continue

    return int(drc_errors), int(unconnected_pads)


def dismiss_already_running():
    # The "Confirmation" modal pops up if pcbnew is already running
    nf_title = TITLE_CONFIRMATION
    wait_for_window(nf_title, nf_title, 1)

    logger.info('Dismiss pcbnew already running')
    xdotool(['search', '--onlyvisible', '--name', nf_title, 'windowfocus'])
    logger.debug('Found, sending Return')
    xdotool(['key', 'Return'])
    logger.debug('Wait a little, this dialog is slow')
    sleep(5)


def dismiss_warning():  # pragma: no cover
    nf_title = TITLE_WARNING
    wait_for_window(nf_title, nf_title, 1)

    logger.error('Dismiss pcbnew warning, will fail')
    xdotool(['search', '--onlyvisible', '--name', nf_title, 'windowfocus'])
    xdotool(['key', 'Return'])


def dismiss_error():
    nf_title = TITLE_ERROR
    wait_for_window(nf_title, nf_title, 1)

    logger.debug('Dismiss pcbnew error')
    xdotool(['search', '--onlyvisible', '--name', nf_title, 'windowfocus'])
    logger.debug('Found, sending Return')
    xdotool(['key', 'Return'])


def wait_pcbnew(time=10, others=None, popen_obj=None):
    return wait_for_window('Main pcbnew window', r'Pcbnew', time, others=others, popen_obj=popen_obj)


def wait_pcbew_start(cfg):
    failed_focuse = False
    other = None
    try:
        wait_pcbnew(args.wait_start, [TITLE_CONFIRMATION, TITLE_WARNING, TITLE_ERROR], cfg.popen_obj)
    except RuntimeError:  # pragma: no cover
        logger.debug('Time-out waiting for pcbnew, will retry')
        failed_focuse = True
    except ValueError as err:
        other = str(err)
        logger.debug('Found "'+other+'" window instead of pcbnew')
        failed_focuse = True
    except subprocess.CalledProcessError:
        logger.debug('Pcbnew is no longer running (returned {})'.format(cfg.popen_obj.poll()))
    if failed_focuse:
        wait_point(cfg)
        if other == TITLE_ERROR:
            dismiss_error()
            logger.error('pcbnew reported an error')
            exit(PCBNEW_ERROR)
        if other == TITLE_CONFIRMATION:
            dismiss_already_running()
        if other == TITLE_WARNING:  # pragma: no cover
            dismiss_warning()
        try:
            wait_pcbnew(5)
        except RuntimeError:  # pragma: no cover
            logger.error('Time-out waiting for pcbnew, giving up')
            exit(PCBNEW_ERROR)


def exit_pcbnew(cfg):
    # Wait until the dialog is closed, useful when more than one file are created
    id = wait_pcbnew(10)

    logger.info('Exiting pcbnew')
    wait_point(cfg)
    xdotool(['key', 'ctrl+q'])
    try:
        wait_not_focused(id[0], 5)
    except RuntimeError:  # pragma: no cover
        logger.debug('PCBnew not exiting, will retry')
        pass
    # Dismiss any dialog. I.e. failed to write the project
    # Note: if we modified the PCB KiCad will ask for save using a broken dialog.
    #       It doesn't have a name and only gets focus with a WM.
    logger.info('Retry pcbnew exit')
    wait_point(cfg)
    xdotool(['key', 'Return', 'ctrl+q'])
    try:
        wait_not_focused(id[0], 5)
    except RuntimeError:  # pragma: no cover
        logger.debug('PCBnew not exiting, will kill')
        pass
    # If we failed to exit we will kill it anyways
    wait_point(cfg)


def open_print_dialog(cfg, print_dialog_keys):
    # Open the KiCad Print dialog
    logger.info('Open File->Print')
    wait_point(cfg)
    xdotool(['key']+print_dialog_keys)
    retry = False
    try:
        id = wait_for_window('Print dialog', 'Print')
    except RuntimeError:  # pragma: no cover
        # Perhaps the fill took too muchm try again
        retry = True
    # Retry the open dialog
    if retry:  # pragma: no cover
        # Excluded from coverage, only happends under conditions hard to reproduce
        logger.info('Open File->Print (retrying)')
        wait_point(cfg)
        xdotool(['key']+print_dialog_keys)
        id = wait_for_window('Print dialog', 'Print')
    return id

def open_3d_view(cfg):
    # Open the KiCad Print dialog
    logger.info('Open View->3D Viewer')
    wait_point(cfg)
    sleep(1*cfg.time_out_scale)
    xdotool(['key', 'alt+3'])
    try:
        id = wait_for_window('3D Viewer', '3D Viewer')
    except RuntimeError:  # pragma: no cover
        return None

    sleep(1*cfg.time_out_scale)

    width = cfg.rec_width
    height = cfg.rec_height
    logger.debug("Moving 3D viewer window...")
    xdotool(['search', '--name', '3D Viewer', 'windowmove', '0', '0'])
    sleep(1*cfg.time_out_scale)

    logger.debug("Resizing 3D viewer window...")
    xdotool(['search', '--name', '3D Viewer', 'windowsize', str(width), str(height)])
    sleep(1*cfg.time_out_scale)

    logger.debug("Waiting for render (sleep 1s)...")
    sleep(1*cfg.time_out_scale)

    return id[0] if id else None


def print_layers(cfg):
    if cfg.kicad_version >= KICAD_VERSION_5_99:
        print_dialog_keys = ['ctrl+p']
    else:
        # We should be able to use Ctrl+P, unless the user configured it
        # otherwise. We aren't configuring hotkeys for 5.1 so is better
        # to just use the menu accelerators (removed on KiCad 6)
        print_dialog_keys = ['alt+f', 'p']
    # Fill zones if the user asked for it
    if cfg.fill_zones:
        logger.info('Fill zones')
        wait_point(cfg)
        # Make sure KiCad is responding
        # We open the dialog and then we close it
        id = open_print_dialog(cfg, print_dialog_keys)
        xdotool(['key', 'Escape'])
        wait_not_focused(id[0])
        wait_pcbnew()
        # Now we fill the zones
        xdotool(['key', 'b'])
        # Wait for complation
        sleep(1)
        wait_pcbnew()
    id = open_print_dialog(cfg, print_dialog_keys)
    # Open the gtk print dialog
    wait_point(cfg)
    # Two possible options here:
    # 1) With WM we usually get "Exclude PCB edge ..." selected
    # 2) Without WM we usually get "Color" selected
    # In both cases sending 4 Shit+Tab moves us to one of the layer columns.
    # From there Return prints and Escape closes the window.
    xdotool(['key', 'shift+Tab', 'shift+Tab', 'shift+Tab', 'shift+Tab', 'Return'])
    # Check it is open
    id2 = wait_for_window('Printer dialog', '^(Print|%s)$' % cfg.print_dlg_name, skip_id=id[0])
    wait_point(cfg)
    # List of printers
    xdotool(['key', 'Tab',
             # Go up to the top
             'Home',
             # Output file name
             'Tab',
             # Open dialog
             'Return'])
    id_sel_f = wait_for_window('Select a filename', '(Select a filename|%s)' % cfg.select_a_filename, 2)
    logger.info('Pasting output dir')
    wait_point(cfg)
    text_replace(cfg.output_file)
    xdotool(['key',
             # Select this name
             'Return'])
    # Back to print
    wait_not_focused(id_sel_f[0])
    wait_for_window('Printer dialog', '^(Print|%s)$' % cfg.print_dlg_name, skip_id=id[0])
    wait_point(cfg)
    xdotool(['key',
             # Format options
             'Tab',
             # Be sure we are at left (PDF)
             'Left', 'Left', 'Left',
             # Print it
             'Return'])
    # Wait until the file is created
    wait_for_file_created_by_process(cfg.pcbnew_pid, cfg.output_file)
    wait_not_focused(id2[1])
    # Now we should be in the KiCad Print dialog again
    id = wait_for_window('Print dialog', 'Print')
    wait_point(cfg)
    # Close the dialog
    # We are in one of the layer columns, here Escape works
    xdotool(['key', 'Escape'])
    wait_not_focused(id2[0])
    # Exit
    exit_pcbnew(cfg)

def _wait_for_pcbnew_idle(timeout):

    def _find_proc(name):
        for proc in psutil.process_iter():
            if proc.name() == name:
                return proc

    render_proc = _find_proc('pcbnew')

    finished = 0
    for t in range(2*timeout):
        sleep(0.5)

        # CPU busy, probably still rendering
        pct = render_proc.cpu_percent()
        print('Rendering... (CPU load {}%)'.format(pct), flush=True)
        if pct > 5:
            finished = 0

        # CPU idle 3 times in a row: rendering probably complete
        else:
            finished+=1
            if finished >= 4:
                return True
            
    # Timeout
    return False


    start = time.time()
    while time.time() < start + RENDER_TIMEOUT:
                cpu = proc.cpu_percent(interval=1)
                print(f'CPU={cpu}', flush=True)
                if cpu < 5:
                    print('Render took %d seconds' % (time.time() - start))
                    return

def render_3d(cfg):
    logger.info("Creating 3D render..")

    # TODO test with Kicad V6
    if cfg.kicad_version >= KICAD_VERSION_5_99:
        logger.warning("Note: render_3d has never been tested with Kicad V6!")
    

    logger.debug("Preparing PCBnew window...")
    id = wait_pcbnew(5)
    xdotool(['windowmove', '--sync', id[0], '0', '0'])
    xdotool(['windowsize', '--sync', id[0], str(cfg.rec_width), str(cfg.rec_height)])
    xdotool(['windowfocus', '--sync', id[0]])

    id_3d = open_3d_view(cfg)
    if id_3d is None:
        logger.warning("Failed to open 3D Viewer")
        return
    _wait_for_pcbnew_idle(timeout=30)
    logger.debug("Zoom to fit..") # Actually zoom to fit + 1x zoom-in
    xdotool(['key', '--window', id_3d, 'Home', 'F1'])
    _wait_for_pcbnew_idle(timeout=30)

    logger.debug("Render with raytracing..")
    xdotool(['key', '--window', id_3d, 'alt+p', 'Down', 'Return'])
    _wait_for_pcbnew_idle(timeout=300)

    logger.debug("File -> Export current view as PNG")
    # File -> Export current view as PNG
    xdotool(['key', '--window', id_3d, 'alt+f', 'Return'])
    # Select all
    xdotool(['key', '--window', id_3d, 'ctrl+a'])
    logger.debug("typing '{}'...".format(cfg.output_file))
    # Type the filename
    xdotool(['type', cfg.output_file])
    xdotool(['key', '--window', id_3d, 'Return'])

    logger.debug("waiting for output file to be written...")
    wait_for_file_created_by_process(cfg.pcbnew_pid, cfg.output_file)
    logger.info("3D view saved as {}".format(cfg.output_file))


def run_drc_5_1(cfg):
    logger.info('Open Inspect->DRC')
    wait_point(cfg)
    xdotool(['key', 'alt+i', 'd'])

    wait_for_window('DRC modal window', 'DRC Control')
    # Note: Refill zones on DRC gets saved in ~/.config/kicad/pcbnew as RefillZonesBeforeDrc
    # The space here is to enable the report of all errors for tracks
    logger.info('Enable reporting all errors for tracks')
    wait_point(cfg)
    xdotool(['key', 'Tab', 'Tab', 'Tab', 'Tab', 'space', 'Tab', 'Tab', 'Tab', 'Tab'])
    logger.info('Pasting output dir')
    wait_point(cfg)
    text_replace(cfg.output_file)
    xdotool(['key', 'Return'])

    wait_for_window('Report completed dialog', 'Disk File Report Completed')
    wait_point(cfg)
    xdotool(['key', 'Return'])
    wait_for_window('DRC modal window', 'DRC Control')

    logger.info('Closing the DRC dialog')
    wait_point(cfg)
    xdotool(['key', 'shift+Tab', 'Return'])
    wait_pcbnew()


def run_drc_6_0(cfg):
    logger.info('Open Inspect->DRC')
    wait_point(cfg)
    xdotool(['key', 'ctrl+shift+i'])
    # Wait dialog
    wait_for_window('DRC modal window', 'DRC Control')
    # Run the DRC
    logger.info('Run DRC')
    wait_point(cfg)
    xdotool(['key', 'Return'])
    #
    # To know when KiCad finished we try this:
    # - Currently I can see a way, just wait some time
    #
    sleep(12*cfg.time_out_scale)
    # Save the DRC
    logger.info('Open the save dialog')
    wait_point(cfg)
    logger.info('Save DRC')
    wait_point(cfg)
    xdotool(['key', 'shift+Tab', 'shift+Tab', 'shift+Tab', 'shift+Tab', 'shift+Tab', 'Return'])
    # Wait for the save dialog
    wait_for_window('DRC File save dialog', 'Save Report to File')
    # Paste the name
    logger.info('Pasting output file')
    wait_point(cfg)
    text_replace(cfg.output_file)
    # Wait for report created
    logger.info('Wait for DRC file creation')
    wait_point(cfg)
    xdotool(['key', 'Return'])
    wait_for_file_created_by_process(cfg.pcbnew_pid, cfg.output_file)
    # Close the dialog
    logger.info('Closing the DRC dialog')
    wait_point(cfg)
    xdotool(['key', 'Escape'])
    wait_pcbnew()


def run_drc_python(cfg):
    logger.debug("Using Python interface instead of running KiCad")
    import pcbnew
    logger.debug("Re-filling zones")
    filler = pcbnew.ZONE_FILLER(cfg.board)
    filler.Fill(cfg.board.Zones())
    logger.debug("Running DRC")
    pcbnew.WriteDRCReport(cfg.board, cfg.output_file, pcbnew.EDA_UNITS_MILLIMETRES, True)
    if cfg.save:
        logger.info('Saving PCB')
        os.rename(cfg.input_file, cfg.input_file + '-bak')
        cfg.board.Save(cfg.input_file)


def run_drc(cfg):
    if cfg.kicad_version >= KICAD_VERSION_5_99:
        run_drc_6_0(cfg)
    else:
        run_drc_5_1(cfg)
    # Save the PCB
    if cfg.save:
        logger.info('Saving PCB')
        wait_point(cfg)
        os.rename(cfg.input_file, cfg.input_file + '-bak')
        xdotool(['key', 'ctrl+s'])
        logger.info('Wait for PCB file creation')
        wait_point(cfg)
        wait_for_file_created_by_process(cfg.pcbnew_pid, os.path.realpath(cfg.input_file))
    # Exit
    exit_pcbnew(cfg)


def load_layers(pcb):
    layer_names = ['-']*50
    with open(pcb, "rt") as pcb_file:
        collect_layers = False
        for line in pcb_file:
            if collect_layers:
                z = re.match(r'\s+\((\d+)\s+"[^"]+"\s+\S+\s+"([^"]+)"', line)
                if not z:
                    z = re.match(r'\s+\((\d+)\s+(\S+)', line)
                if z:
                    id, name = z.groups()
                    if name[0] == '"':
                        name = name[1:-1]
                    layer_names[int(id)] = name
                else:
                    if re.search(r'^\s+\)$', line):
                        collect_layers = False
                        break
            else:
                if re.search(r'\s+\(layers', line):
                    collect_layers = True
    return layer_names


class ListLayers(argparse.Action):
    """A special action class to list the PCB layers and exit"""
    def __call__(self, parser, namespace, values, option_string):
        layer_names = load_layers(values[0])
        for layer in layer_names:
            if layer != '-':
                print(layer)
        parser.exit()  # exits the program with no more arg parsing and checking


def restore_pcb(cfg):
    if cfg.input_file and cfg.pcb_size >= 0 and cfg.pcb_date >= 0:
        cur_date = os.path.getmtime(cfg.input_file)
        bkp = cfg.input_file+'-bak'
        if cur_date != cfg.pcb_date:
            logger.debug('Current pcb date: {} (!={}), trying to restore it'.
                         format(asctime(localtime(cur_date)), asctime(localtime(cfg.pcb_date))))
            if os.path.isfile(bkp):
                bkp_size = os.path.getsize(bkp)
                if bkp_size == cfg.pcb_size:
                    os.remove(cfg.input_file)
                    os.rename(bkp, cfg.input_file)
                    logger.debug('Moved {} -> {}'.format(bkp, cfg.input_file))
                else:  # pragma: no cover
                    logger.error('Corrupted back-up file! (size = {})'.format(bkp_size))
            else:  # pragma: no cover
                logger.error('No back-up available!')
        if cfg.kicad_version >= KICAD_VERSION_5_99 and os.path.isfile(bkp):
            os.remove(bkp)


def memorize_pcb(cfg):
    cfg.pcb_size = os.path.getsize(cfg.input_file)
    cfg.pcb_date = os.path.getmtime(cfg.input_file)
    logger.debug('Current pcb ({}) size: {} date: {}'.
                 format(cfg.input_file, cfg.pcb_size, asctime(localtime(cfg.pcb_date))))
    if cfg.kicad_version >= KICAD_VERSION_5_99:
        # KiCad 6 no longer creates back-up, we do it
        shutil.copy2(cfg.input_file, cfg.input_file+'-bak')
    atexit.register(restore_pcb, cfg)


def create_pcbnew_config(cfg):
    # Mark which layers are requested
    used_layers = set()
    layer_cnt = cfg.board.GetCopperLayerCount()
    for layer in cfg.layers:
        # Support for kiplot inner layers
        if layer.startswith("Inner"):
            m = re.match(r"^Inner\.([0-9]+)$", layer)
            if not m:
                logger.error('Malformed inner layer name: '+layer+', use Inner.N')
                sys.exit(WRONG_LAYER_NAME)
            layer_n = int(m.group(1))
            if layer_n < 1 or layer_n >= layer_cnt - 1:
                logger.error(layer+" isn't a valid layer")
                sys.exit(WRONG_LAYER_NAME)
            used_layers.add(layer_n)
        else:
            id = cfg.board.GetLayerID(layer)
            if id < 0:
                logger.error('Unknown layer '+layer)
                sys.exit(WRONG_LAYER_NAME)
            used_layers.add(id)
    with open(cfg.conf_pcbnew, "wt") as text_file:
        if cfg.conf_pcbnew_json:
            conf = {"graphics": {"canvas_type": 2}}
            conf["drc_dialog"] = {"refill_zones": True,
                                  "test_track_to_zone": True,
                                  "test_all_track_errors": True}
            conf["system"] = {"first_run_shown": True}
            conf["printing"] = {"monochrome": cfg.monochrome,
                                # TODO: Allow configuration
                                "color_theme": "_builtin_classic",
                                "use_theme": True,
                                "title_block": not cfg.no_title,
                                "scale": cfg.scaling,
                                "layers": sorted(used_layers)}
            conf["plot"] = {"check_zones_before_plotting": cfg.fill_zones,
                            "mirror": cfg.mirror,
                            "all_layers_on_one_page": int(not cfg.separate),
                            "pads_drill_mode": cfg.pads}
            conf["window"] = {"size_x": cfg.rec_width,
                              "size_y": cfg.rec_height}
            json_text = json.dumps(conf)
            text_file.write(json_text)
            logger.debug(json_text)
        else:
            text_file.write('canvas_type=2\n')
            text_file.write('RefillZonesBeforeDrc=1\n')
            text_file.write('DrcTrackToZoneTest=1\n')
            text_file.write('PcbFrameFirstRunShown=1\n')
            # Color
            text_file.write('PrintMonochrome=%d\n' % (cfg.monochrome))
            # Include frame
            text_file.write('PrintPageFrame=%d\n' % (not cfg.no_title))
            # Drill marks
            text_file.write('PrintPadsDrillOpt=%d\n' % (cfg.pads))
            # Only one file
            text_file.write('PrintSinglePage=%d\n' % (not cfg.separate))
            # Scaling
            if int(cfg.scaling) == 1:
                text_file.write('PrintScale=0\n')
            elif cfg.scaling:
                text_file.write('PrintScale=%3.1f\n' % (cfg.scaling))
            else:
                text_file.write('PrintScale=1\n')
            # List all posible layers, indicating which ones are requested
            for x in range(0, 50):
                text_file.write('PlotLayer_%d=%d\n' % (x, int(x in used_layers)))


def load_pcb(fname):
    import pcbnew
    try:
        board = pcbnew.LoadBoard(fname)
    except OSError as e:
        logger.error('Error loading PCB file. Corrupted?')
        logger.error(e)
        exit(CORRUPTED_PCB)
    return board


def process_drc_out(cfg):
    error_level = 0
    drc_errors, unconnected_pads = parse_drc(cfg)
    logger.debug('Found {} DRC errors and {} unconnected pads'.format(drc_errors, unconnected_pads))
    # Apply filters
    skip_err, skip_unc = apply_filters(cfg, 'DRC error/s', 'unconnected pad/s')
    drc_errors = drc_errors-skip_err
    unconnected_pads = unconnected_pads-skip_unc
    if drc_errors == 0 and unconnected_pads == 0:
        logger.info('No errors')
    else:
        logger.error('Found {} DRC errors and {} unconnected pads'.format(drc_errors, unconnected_pads))
        list_errors(cfg)
        if args.ignore_unconnected:
            unconnected_pads = 0
        else:
            list_warnings(cfg)
        error_level = -(drc_errors+unconnected_pads)
    return error_level


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='KiCad PCB automation')
    subparsers = parser.add_subparsers(help='Command:', dest='command')

    # short commands: rmsvVw
    parser.add_argument('--record', '-r', help='Record the UI automation', action='store_true')
    parser.add_argument('--rec_width', help='Record width ['+str(REC_W)+']', type=int, default=REC_W)
    parser.add_argument('--rec_height', help='Record height ['+str(REC_H)+']', type=int, default=REC_H)
    parser.add_argument('--start_x11vnc', '-s', help='Start x11vnc (debug)', action='store_true')
    parser.add_argument('--use_wm', '-m', help='Use a window manager (fluxbox)', action='store_true')
    parser.add_argument('--verbose', '-v', action='count', default=0)
    parser.add_argument('--version', '-V', action='version', version='%(prog)s '+__version__+' - ' +
                        __copyright__+' - License: '+__license__)
    parser.add_argument('--wait_key', '-w', help='Wait for key to advance (debug)', action='store_true')
    parser.add_argument('--wait_start', help='Timeout to pcbnew start ['+str(WAIT_START)+']', type=int, default=WAIT_START)
    parser.add_argument('--time_out_scale', help='Timeout multiplier, affects most timeouts',
                        type=float, default=TIME_OUT_MULT)

    # short commands: flmMopsSt
    export_parser = subparsers.add_parser('export', help='Export PCB layers')
    export_parser.add_argument('--fill_zones', '-f', help='Fill all zones before printing', action='store_true')
    export_parser.add_argument('--list', '-l', help='Print a list of layers in LIST PCB and exit', nargs=1, action=ListLayers)
    export_parser.add_argument('--output_name', '-o', nargs=1, help='Name of the output file', default=['printed.pdf'])
    export_parser.add_argument('--scaling', '-s', nargs=1, help='Scale factor (0 fit page)', default=[1.0])
    export_parser.add_argument('--pads', '-p', nargs=1, help='Pads style (0 none, 1 small, 2 full)', default=[2])
    export_parser.add_argument('--no-title', '-t', help='Remove the title-block', action='store_true')
    export_parser.add_argument('--monochrome', '-m', help='Print in blanck and white', action='store_true')
    export_parser.add_argument('--mirror', '-M', help='Print mirrored', action='store_true')
    export_parser.add_argument('--separate', '-S', help='Layers in separated sheets', action='store_true')
    export_parser.add_argument('kicad_pcb_file', help='KiCad PCB file')
    export_parser.add_argument('output_dir', help='Output directory')
    export_parser.add_argument('layers', nargs='+', help='Which layers to include')


    # short commands: o
    render_3d_parser = subparsers.add_parser('render_3d', help='Export 3D render')
    render_3d_parser.add_argument('--output_name', '-o', nargs=1, help='Name of the output file', default=['render.png'])
    render_3d_parser.add_argument('kicad_pcb_file', help='KiCad PCB file')
    render_3d_parser.add_argument('output_dir', help='Output directory')
    # TODO resolution, rotattion

    # short commands: ios
    drc_parser = subparsers.add_parser('run_drc', help='Run Design Rules Checker on a PCB')
    drc_parser.add_argument('--errors_filter', '-f', nargs=1, help='File with filters to exclude errors')
    drc_parser.add_argument('--ignore_unconnected', '-i', help='Ignore unconnected paths', action='store_true')
    drc_parser.add_argument('--output_name', '-o', nargs=1, help='Name of the output file', default=['drc_result.rpt'])
    drc_parser.add_argument('--save', '-s', help='Save after DRC (updating filled zones)', action='store_true')
    drc_parser.add_argument('kicad_pcb_file', help='KiCad PCB file')
    drc_parser.add_argument('output_dir', help='Output directory')

    args = parser.parse_args()
    # Set the specified verbosity
    log.set_level(logger, args.verbose)

    if args.command is None:
        logger.error('No command selected')
        parser.print_help()
        exit(WRONG_ARGUMENTS)

    cfg = Config(logger, args.kicad_pcb_file, args)
    set_time_out_scale(cfg.time_out_scale)
    # Empty values by default, we'll fill them for export
    cfg.fill_zones = False
    cfg.layers = []
    cfg.save = args.command == 'run_drc' and args.save
    cfg.input_file = args.kicad_pcb_file

    # Get local versions for the GTK window names
    gettext.textdomain('gtk30')
    cfg.select_a_filename = gettext.gettext('Select a filename')
    cfg.print_dlg_name = gettext.gettext('Print')
    logger.debug('Select a filename -> '+cfg.select_a_filename)
    logger.debug('Print -> '+cfg.print_dlg_name)

    # Force english + UTF-8
    os.environ['LANG'] = 'C.UTF-8'
    # Make sure the input file exists and has an extension
    check_input_file(cfg, NO_PCB, WRONG_PCB_NAME)
    cfg.board = load_pcb(cfg.input_file)
    if not cfg.save:
        memorize_pcb(cfg)

    if args.command == 'export':
        # Read the layer names from the PCB
        cfg.fill_zones = args.fill_zones
        cfg.layers = args.layers
        try:
            cfg.scaling = float(args.scaling[0])
        except ValueError:
            logger.error('Scaling must be a floating point value')
            exit(WRONG_ARGUMENTS)
        try:
            cfg.pads = int(args.pads[0])
        except ValueError:
            logger.error('Pads style must be an integer value')
            exit(WRONG_ARGUMENTS)
        if cfg.pads < 0 or cfg.pads > 2:
            logger.error('Pad style must be 0, 1 or 2')
            exit(WRONG_ARGUMENTS)
        cfg.no_title = args.no_title
        cfg.monochrome = args.monochrome
        cfg.separate = args.separate
        cfg.mirror = args.mirror
        if args.mirror and cfg.kicad_version < KICAD_VERSION_5_99:
            logger.warning("KiCad 5 doesn't support setting mirror print from the configuration file")
    else:
        cfg.scaling = 1.0
        cfg.pads = 2
        cfg.no_title = False
        cfg.monochrome = False
        cfg.separate = False
        cfg.mirror = False

    if args.command == 'run_drc' and args.errors_filter:
        load_filters(cfg, args.errors_filter[0])

    if args.command == 'render_3d':
        # TODO parse args here
        pass

    memorize_project(cfg)
    # Back-up the current pcbnew configuration
    check_kicad_config_dir(cfg)
    cfg.conf_pcbnew_bkp = backup_config('PCBnew', cfg.conf_pcbnew, PCBNEW_CFG_PRESENT, cfg)
    # Create a suitable configuration
    create_pcbnew_config(cfg)
    if cfg.kicad_version >= KICAD_VERSION_5_99:
        # KiCad 6 breaks menu short-cuts, but we can configure user hotkeys
        # Back-up the current user.hotkeys configuration
        cfg.conf_hotkeys_bkp = backup_config('User hotkeys', cfg.conf_hotkeys, USER_HOTKEYS_PRESENT, cfg)
        # Create a suitable configuration
        create_user_hotkeys(cfg)
    # Make sure the user has fp-lib-table
    check_lib_table(cfg.user_fp_lib_table, cfg.sys_fp_lib_table)
    # Create output dir, compute full name for output file and remove it
    output_dir = os.path.abspath(args.output_dir)
    cfg.video_dir = cfg.output_dir = output_dir
    os.makedirs(output_dir, exist_ok=True)
    # Remove the output file
    output_file = os.path.join(output_dir, args.output_name[0])
    if os.path.exists(output_file):
        os.remove(output_file)
    cfg.output_file = output_file
    # Name for the video
    cfg.video_name = 'pcbnew_'+args.command+'_screencast.ogv'
    #
    # Do all the work
    #
    error_level = 0
    if args.command == 'run_drc' and cfg.kicad_version >= KICAD_VERSION_5_99:
        # First command to migrate to Python!
        run_drc_python(cfg)
        error_level = process_drc_out(cfg)
        do_retry = False
    else:
        flog_out, flog_err = get_log_files(output_dir, 'pcbnew')
        for retry in range(3):
            do_retry = False
            with recorded_xvfb(cfg, retry):
                logger.debug('Starting '+cfg.pcbnew)
                with PopenContext([cfg.pcbnew, cfg.input_file], stderr=flog_err, close_fds=True,
                                  stdout=flog_out, start_new_session=True) as pcbnew_proc:
                    cfg.pcbnew_pid = pcbnew_proc.pid
                    cfg.popen_obj = pcbnew_proc
                    wait_pcbew_start(cfg)
                    if pcbnew_proc.poll() is not None:
                        do_retry = True
                    else:
                        if args.command == 'export':
                            print_layers(cfg)
                        elif args.command == 'render_3d':
                            render_3d(cfg)

                        else:  # run_drc
                            run_drc(cfg)
                            error_level = process_drc_out(cfg)
            if not do_retry:
                break
            logger.warning("Pcbnew failed to start retrying ...")
    if do_retry:
        logger.error("Pcbnew failed to start try with --time_out_scale")
        error_level = PCBNEW_ERROR
    #
    # Exit clean-up
    #
    # The following code is here only to make coverage tool properly meassure atexit code.
    if not cfg.save:
        atexit.unregister(restore_pcb)
        restore_pcb(cfg)
    atexit.unregister(restore_config)
    restore_config(cfg)
    atexit.unregister(restore_project)
    restore_project(cfg)
    exit(error_level)

