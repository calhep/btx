import numpy as np
import argparse
import os
import subprocess
import requests

class Indexer:
    
    """ 
    Wrapper for writing executable to index cxi files using CrystFEL's indexamajig 
    and reporting those results to a summary file and the elog.
    """

    def __init__(self, exp, run, det_type, tag, taskdir, geom, cell=None, int_rad='4,5,6', methods='mosflm',
                 tolerance='5,5,5,1.5', tag_cxi=None, no_revalidate=True, multi=True, profile=True):
        
        # general paramters
        self.exp = exp
        self.run = run
        self.det_type = det_type

        self.taskdir = taskdir
        self.tag = tag
        self.tag_cxi = tag_cxi
        
        # indexing parameters
        self.geom = geom # geometry file in CrystFEL format
        self.cell = cell # file containing unit cell information
        self.rad = int_rad # list of str, radii of integration
        self.methods = methods # str, indexing packages to run
        self.tolerance = tolerance # list of str, tolerances for unit cell comparison
        self.no_revalidate = no_revalidate # bool, skip validation step to omit iffy peaks
        self.multi = multi # bool, enable multi-lattice indexing
        self.profile = profile # bool, display timing data
        self._retrieve_paths()
        self._parallel_logic()

    def _parallel_logic(self):
        """
        Retrieve number of processors to run indexamajig on. If running in 
        parallel, import mpi4py to ensure only first rank writes outfile.
        """
        self.nproc = os.environ['NCORES']
        if int(self.nproc) > 1:
            from mpi4py import MPI
            comm = MPI.COMM_WORLD
            self.rank = comm.Get_rank()
        else:
            self.rank = 0

    def _retrieve_paths(self):
        """
        Retrieve the paths for the input .lst and output .stream file 
        consistent with the btx analysis directory structure.
        """
        if self.tag_cxi is not None :
            if ( self.tag_cxi != '' ) and ( self.tag_cxi[0]!='_' ):
                self.tag_cxi = '_'+self.tag_cxi
        else:
            self.tag_cxi = ''
        self.lst = os.path.join(self.taskdir ,f'r{self.run:04}/r{self.run:04}{self.tag_cxi}.lst')
        self.stream = os.path.join(self.taskdir, f'r{self.run:04}_{self.tag}.stream')
        if "TMP_EXE" in os.environ:
            self.tmp_exe = os.environ['TMP_EXE']
        else:
            self.tmp_exe = os.path.join(self.taskdir ,f'r{self.run:04}/index_r{self.run:04}.sh')
        self.peakfinding_summary = os.path.join(self.taskdir ,f'r{self.run:04}/peakfinding{self.tag_cxi}.summary')
        self.indexing_summary = os.path.join(self.taskdir ,f'r{self.run:04}/indexing_{self.tag}.summary')

        self.script_path = os.path.abspath(__file__)
        self.python_path = os.environ['WHICHPYTHON']

    def write_exe(self):
        """
        Write an indexing executable for submission to slurm.
        """     
        if self.rank == 0:
            command=f"indexamajig -i {self.lst} -o {self.stream} -j {self.nproc} -g {self.geom} --peaks=cxi --int-rad={self.rad} --indexing={self.methods} --tolerance={self.tolerance}"
            if self.cell is not None: command += f' --pdb={self.cell}'
            if self.no_revalidate: command += ' --no-revalidate'
            if self.multi: command += ' --multi'
            if self.profile: command += ' --profile'

            command_report=f"{self.python_path} {self.script_path} -e {self.exp} -r {self.run} -d {self.det_type} --taskdir {self.taskdir} --report --tag {self.tag} "
            if ( self.tag_cxi != '' ): command_report += f' --tag_cxi {self.tag_cxi}'

            with open(self.tmp_exe, 'w') as f:
                f.write("#!/bin/bash\n")
                f.write(f"{command}\n")
                f.write(f"{command_report}\n")
            print(f"Indexing executable written to {self.tmp_exe}")
            
    def report(self, update_url=None):
        """
        Write results to a .summary file and optionally post to the elog.
        
        Parameters
        ----------
        update_url : str
            elog URL for posting progress update
        """
        if self.rank == 0:
            # retrieve number of indexed patterns
            command = ["grep", "Cell parameters", f"{self.stream}"]
            output,error  = subprocess.Popen(
                                command, universal_newlines=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE).communicate()
            n_indexed = len(output.split('\n')[:-1])
            
            # retrieve number of total patterns
            command = ["grep", "Number of hits found", f"{self.peakfinding_summary}"]
            output,error  = subprocess.Popen(
                                command, universal_newlines=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE).communicate()
            print(self.peakfinding_summary)

            n_total = int(output.split(":")[1].split("\n")[0])
            
            # write summary file
            with open(self.indexing_summary, 'w') as f:
                f.write(f"Number of indexed events: {n_indexed}\n")
                f.write(f"Fractional indexing rate rate: {(n_indexed/n_total):.2f}\n")

            # post to elog
            update_url = os.environ.get('JID_UPDATE_COUNTERS')
            if update_url is not None:
                # retrieve results from peakfinding.summary, since these will be overwritten in the elog
                with open(self.peakfinding_summary, "r") as f:
                    lines = f.readlines()[:3]
                pf_keys = [item.split(":")[0] for item in lines]
                pf_vals = [item.split(":")[1].strip(" ").strip('\n') for item in lines]

                try:
                    requests.post(update_url, json=[{ "key": f"{pf_keys[0]}", "value": f"{pf_vals[0]}"},
                                                    { "key": f"{pf_keys[1]}", "value": f"{pf_vals[1]}"},
                                                    { "key": f"{pf_keys[2]}", "value": f"{pf_vals[2]}"},
                                                    { "key": "Number of indexed events", "value": f"{n_indexed}"},
                                                    { "key": "Fractional indexing rate", "value": f"{(n_indexed/n_total):.2f}"}, ])
                except:
                    print("Could not communicate with the elog update url")

def parse_input():
    """
    Parse command line input.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument('-e', '--exp', help='Experiment name', required=True, type=str)
    parser.add_argument('-r', '--run', help='Run number', required=True, type=int)
    parser.add_argument('-d', '--det_type', help='Detector name, e.g epix10k2M or jungfrau4M', required=True, type=str)
    parser.add_argument('--tag', help='Suffix extension for stream file', required=True, type=str)
    parser.add_argument('--tag_cxi', help='Tag to identify input CXI files', required=False, type=str)
    parser.add_argument('--taskdir', help='Base directory for indexing results', required=True, type=str)
    parser.add_argument('--report', help='Report indexing results to summary file and elog', action='store_true')
    parser.add_argument('--update_url', help='URL for communicating with elog', required=False, type=str)
    parser.add_argument('--geom', help='CrystFEL-style geom file, required if not reporting', required=False, type=str)
    parser.add_argument('--cell', help='File containing unit cell information (.pdb or .cell)', required=False, type=str)
    parser.add_argument('--int_rad', help='Integration radii for peak, buffer and background regions', required=False, type=str, default='4,5,6')
    parser.add_argument('--methods', help='Indexing methods', required=False, type=str, default='xgandalf,mosflm,xds')
    parser.add_argument('--tolerance', help='Tolerances for unit cell comparison: a,b,c,ang', required=False, type=str, default='5,5,5,1.5')
    parser.add_argument('--no_revalidate', help='Skip validation step that omits peaks that are saturated, too close to detector edge, etc.', action='store_false')
    parser.add_argument('--multi', help='Enable multi-lattice indexing', action='store_false')
    parser.add_argument('--profile', help='Display timing data', action='store_false')
    
    return parser.parse_args()

if __name__ == '__main__':
    
    params = parse_input()
    
    indexer_obj = Indexer(exp=params.exp, run=params.run, det_type=params.det_type, tag=params.tag, taskdir=params.taskdir, geom=params.geom, 
                          cell=params.cell, int_rad=params.int_rad, methods=params.methods, tolerance=params.tolerance, tag_cxi=params.tag_cxi,
                          no_revalidate=params.no_revalidate, multi=params.multi, profile=params.profile)
    if not params.report:
        indexer_obj.write_exe()
    else:
        indexer_obj.report(params.update_url)
