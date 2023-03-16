#!/usr/bin/env python3

# lwobker@cisco.com    
# usage:  copy script into shell of running XR machine
# to execute, just do "nohup python3 ./drop_script.py -n 60 -t 60 &> npudrops.log &"
# this will run it 60 times, once every 60 seconds, (i.e. for 1 hour) 


import argparse
import datetime
import json
import logging
import lzma
import os
import subprocess
import time


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

def getOutputfile(args, commandOutput):
    """ build the path/filename for the output file"""
    snapshot_dir = args.output_dir
    if not os.path.exists(snapshot_dir):
        os.mkdir(snapshot_dir)
    log.info(f"Using output directory {snapshot_dir}")
    snapTime = datetime.datetime.fromtimestamp(int(commandOutput["timestamp"]))
    timestamp = snapTime.strftime("%y%m%d-%H%M%S")
    filename_leader = args.leader + commandOutput["etcHostname"].strip() 
    out_fname = filename_leader + "_cmds_" + timestamp + ".json.xz"     # assemble output filename
    output_fullpath = "/".join([snapshot_dir, out_fname])
    return output_fullpath

def saveJsonXz(cmdOutput: dict, filename: str) -> None:
    with lzma.open(filename, "wt", encoding='utf-8') as outfile:
        log.info(f"json.dump-ing compressed JSON output to {filename}.")
        json.dump(cmdOutput, outfile, indent=4)

def getParser():
    parser = argparse.ArgumentParser()
    parser.add_argument("-t", "--time_interval", type=int, default=30, help="seconds between subsequent runs - default 30 sec")
    parser.add_argument("-n", "--num_runs", type=int, default=1, help="number of runs to execute - use '0' to run forever")
    parser.add_argument("-o", "--output_dir", default='/var/xr/disk1/envSnaps', help="output directory, default: /var/xr/disk1/envSnaps")
    parser.add_argument("-l", "--leader", default = '', help="descriptive string prepended to snapshot filenames")
    return parser.parse_args()

def runCommands(cmdTable) -> dict:
    """return a dictionary of captured output from commands defined in cmdTable.
    Note: not all of these commands will exist on all systems (e.g. the fabric stuff doesn't 
    exist on a fixed system. 
    """
    procOutput = {}  # dict to store output text from show commands 
    procHandles = {}
    for cmd in cmdTable.keys():
        try:
            procHandles[cmd] = subprocess.Popen(cmdTable[cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        except FileNotFoundError:
            log.debug(f"process handle {cmd} could not execute --  likely because fixed vs. distributed")
    for handle, proc in procHandles.items():
        try:
            procOutput[handle] = proc.communicate(timeout=180)[0]  # turn stdout portion into text
        except subprocess.TimeoutExpired:
            proc.kill()
            raise TimeoutError(f"killed process with handle '{handle}' : timed out")
    return procOutput


runOnceCmdTable = {
    "showVersion": ["show_version"],        # XR: "show version"
    "showIntf": ["show_interface", "-a"],   # XR: "show interface"
    "showInv": ["show_inventory", "-e"],        # XR: show inventory"
    "etcHostname": ["cat", "/etc/hostname"],        # hostname (no direct XR equiv.)
    "showNpuSlice": ["show_slicemgr", "-I", "0xff", "-n", "A"],   # XR: show contr npu slice info...
}

loopCmdTable = {                # will run these each time
    "timestamp": ["date", "+%s"],           # XR: "show clock"
    "showPolMapInt": ["qos_ma_show_stats", "-i", "Bundle-Ether21", "-p", "0x1", "-q", "0x2",]
}

# build the command strings to show voq state for the bundle members we're interested in...  "TenGigE0_11_0_x_y"
bundlemembers = []
for m_port in ['2', '3', '4']:
    for m_breakout in ['0', '1', '2', '3']:
        bundlemembers.append(f"TenGigE0_11_0_{m_port}_{m_breakout}")

# add the member voq commands to the table of commands to run
for card in [0,11]:
    for member in bundlemembers:
        voq_ingress_stats = ["ofa_npu_stats_show", "-v", "a", "-i", "0x10", "-n", "0", "-t", "s", "-p", f"{member}",]
        voq_ingress_stats += ["-s", "0x0", "-d", "0x0", "-T", "0x0", "-P", "0xffffffff", "-c", "0xff",]
        loopCmdTable[f"voqs_member{member}"] = voq_ingress_stats

    for npu_inst in range(3):
            # create commands to clear the counters (we run this once at the beginning)
        clrNpuCmd = ["npd_npu_driver_clear", "-c", "s", "-i", f"0x{str(npu_inst)}", "-n", f"{str(256*card)}"]
        runOnceCmdTable[f"clear_command_{card}_{npu_inst}"] = clrNpuCmd
            # build the show command for each (LC, NPU) tuple
        npuStats = ["ofa_npu_stats_show", "-v", "a", "-t", "e", "-p", "0xffffffff", "-s", "0x0", "-d", "A",]
        npuStats = npuStats + ["-i", f"0x{str(npu_inst)}", "-n", f"{str(256*card)}"]
        read_dvoq = ["npu_driver_show", "-c", "script read_dvoq_qsm", "-u", f"0x{npu_inst}", "-n", f"{str(256*card)}",]
        oq_debug = ["npu_driver_show", "-c", "script sf_oq_debug_full true", "-u", f"0x{npu_inst}", "-n", f"{str(256*card)}",]
        summ_ctrs = ["npu_driver_show", "-c", "script print_get_counters true", "-u", f"0x{npu_inst}", "-n", f"{str(256*card)}",]
            # add them to the list of commands to loop...
        loopCmdTable[f"npu_drops{card}_{npu_inst}"] = npuStats
        loopCmdTable[f"dvoq_check{card}_{npu_inst}"] = read_dvoq
        loopCmdTable[f"oq_debug_full{card}_{npu_inst}"] = oq_debug
        loopCmdTable[f"summ_ctrs{card}_{npu_inst}"] = summ_ctrs


if __name__ == '__main__':
    os.nice(20)
    args = getParser()
    commandOutput = runCommands(runOnceCmdTable)   # run once
    run_counter = 0
    finished = False
    while not finished:         # run main loop of commands
        run_counter += 1
        commandOutput.update(runCommands(loopCmdTable))       
        output_fullpath = getOutputfile(args, commandOutput)
        saveJsonXz(commandOutput, output_fullpath)
        if (run_counter >= args.num_runs):    # are we done?
            finished = True
        else:
            time.sleep(args.time_interval)

exit(0)

