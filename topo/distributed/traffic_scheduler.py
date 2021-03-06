from typing import Dict, List
import threading
import subprocess
import time
import os
from path_utils import get_prj_root
import threading
from utils.log_utils import debug, info, err
import signal
from collections import defaultdict
import math
from subprocess import DEVNULL

import random

target_id_dir = os.path.join(get_prj_root(), "topo/distributed/targetids")


def run_ns_binary(ns: str, bin: str, params: str, log_fn: str = "/tmp/log.log"):
	os.system("ip netns exec {} nohup {} {} >{} 2>&1 &".format(ns, bin, params, log_fn))


def kill_pid(pid):
	os.system("kill -9 {}".format(pid))


class BasicTrafficScheduler:
	def __init__(self, config: Dict, hostids: List[int]):
		self.config = config
		self.hostids = hostids
		self.generator_id = 0

		self.genid2pid = {}
		self.pid2genid = {}
		self.binary = self.config["traffic_generator"]

		# self.traffic_scales = ["small", "small", "small", "small"]
		self.traffic_scales = self.config["traffic_mode"]
		self.durations = self.config["traffic_duration"]
		assert len(self.traffic_scales) == len(self.durations)
		# self.durations = [120, 120, 120, 120]
		self.flow_types = ["iot", "video", "voip"]

	def _do_start_traffic(self, hid, flow_type) -> (int, int):
		hostname = "h{}".format(hid)
		intf = "{}-eth0".format(hostname)
		target_id_fn = os.path.join(target_id_dir, "{}.targetids".format(hostname))
		gen_id = self.generator_id
		self.generator_id += 1
		log_fn = "/tmp/{}.{}.gen.log".format(hostname, gen_id)
		pkt_dir = self.config["traffic_dir"][flow_type]
		ftype = 0
		if flow_type == "video":
			ftype = 0
		elif flow_type == "iot":
			ftype = 1
		elif flow_type == "voip":
			ftype = 2
		else:
			raise Exception("Unsupported flow type")

		controller_ip = self.config["controller"].split(":")[0]

		params = "--id {} " \
		         "--dst_id {} " \
		         "--pkts {} " \
		         "--mtu {} " \
		         "--int {} " \
		         "--cip {} " \
		         "--ftype {}" \
		         "--cport {}".format(
			hid,
			target_id_fn,
			pkt_dir,
			self.config["vhost_mtu"],
			intf,
			controller_ip,
			ftype,
			self.config["controller_socket_port"],
		)

		commands = "nohup ip netns exec {} {} {}".format(hostname, self.binary, params)
		pid = subprocess.Popen(commands.split(" "), stdout=DEVNULL, stderr=DEVNULL).pid
		return pid, self.generator_id

	def _do_traffic_schedule(self):
		raise NotImplementedError

	def _do_stop_traffic_schedule(self):
		raise NotImplementedError

	def start(self):
		threading.Thread(target=self._do_traffic_schedule).start()

	def stop(self):
		self._do_stop_traffic_schedule()


class TrafficScheduler(BasicTrafficScheduler):
	def __init__(self, config: Dict, hostids: List[int]):
		super(TrafficScheduler, self).__init__(config, hostids)

		self.cv = threading.Condition()

		self.processes = {
			"iot": [],
			"video": [],
			"voip": []
		}

	def _stop_traffic(self, pid, flow_type):
		kill_pid(pid)
		genid = self.pid2genid[pid]
		del self.pid2genid[pid]
		del self.genid2pid[genid]
		self.processes[flow_type].remove(pid)

	def _start_traffic(self, hid, flow_type):
		pid, genid = self._do_start_traffic(hid, flow_type)
		self.pid2genid[pid] = genid
		self.genid2pid[genid] = pid
		self.processes[flow_type].append(pid)

	def _do_traffic_schedule(self):
		debug("start traffic schedule in thread:#{}".format(threading.get_ident()))

		traffic_scale_idx = 0

		basic_n_video = self.config["num_process"]["video"][0]
		medium_n_video = self.config["num_process"]["video"][1]
		large_n_video = self.config["num_process"]["video"][2]

		flow_types = ["iot", "video", "voip"]
		for ft in flow_types:
			for _ in range(self.config["num_process"][ft][0]):
				for hid in self.hostids:
					self._start_traffic(hid, ft)

		self.cv.acquire()
		# ?????????small???????????????
		# ?????????????????????medium???????????????
		# ??????large???????????????

		while True:
			# ?????????????????????????????????,???????????????????????????????????????????????????
			video_processes = self.processes["video"]
			# debug("")
			n_video_process = len(video_processes)

			scale = self.traffic_scales[traffic_scale_idx]
			duration = self.durations[traffic_scale_idx]
			target_n_video = -1
			if scale == "small":
				target_n_video = basic_n_video
			elif scale == "medium":
				target_n_video = medium_n_video
			elif scale == "large":
				target_n_video = large_n_video
			else:
				err("Invalid traffic scale {}".format(scale))
				exit(-1)
			target_n_video = len(self.hostids) * target_n_video

			if n_video_process > target_n_video:
				# ????????????
				to_bekilled_pids = video_processes[target_n_video:]
				for pid in to_bekilled_pids:
					self._stop_traffic(pid, "video")

			# ????????????
			elif n_video_process < target_n_video:
				num_add = target_n_video - n_video_process
				for _ in range(num_add // len(self.hostids)):
					for hid in self.hostids:
						self._start_traffic(hid, "video")

			debug(self.processes)
			# ??????
			if not self.cv.wait(duration):
				traffic_scale_idx = (traffic_scale_idx + 1) % len(self.durations)
				debug("traffic mode changed to {}".format(self.traffic_scales[traffic_scale_idx]))
				continue
			else:
				debug("Exit traffic scheduler")
				self.cv.release()
				break

	def _do_stop_traffic_schedule(self):
		self.cv.acquire()
		self.cv.notify()
		self.cv.release()
		# remove all traffic generators
		# kill all genertor
		for pid in self.pid2genid.keys():
			kill_pid(pid)

		# ????????????
		os.system("for p in `pgrep '^gen$'`;do kill -9 $p;done")
		self.processes = {
			"iot": [],
			"video": [],
			"voip": []
		}
		self.genid2pid = {}
		self.pid2genid = {}


class TrafficScheduler2(BasicTrafficScheduler):
	def __init__(self, config: Dict, hostids: List[int]):
		super(TrafficScheduler2, self).__init__(config, hostids)
		self.cv = threading.Condition()
		self.processes = {
			"iot": [],
			"video": [],
			"voip": []
		}

		# ?????????????????????????????????
		self.schedule_record = []
		random.seed(int(time.time()))

	def _start_traffic(self, hid, flow_type, to_schedule=False):
		pid, genid = self._do_start_traffic(hid, flow_type)
		self.pid2genid[pid] = genid
		self.genid2pid[genid] = pid
		self.processes[flow_type].append(pid)

		if to_schedule:
			self.schedule_record.append(pid)

	def _stop_traffic(self, pid, flow_type, to_schedule=True):
		kill_pid(pid)
		genid = self.pid2genid[pid]
		del self.pid2genid[pid]
		del self.genid2pid[genid]
		self.processes[flow_type].remove(pid)

		if to_schedule:
			self.schedule_record.remove(pid)

	def _do_traffic_schedule(self):
		debug("start traffic schedule in thread:#{}".format(threading.get_ident()))

		flow_types = self.flow_types
		for ft in flow_types:
			for _ in range(self.config["num_process"][ft][0]):
				for hid in self.hostids:
					self._start_traffic(hid, ft)
		debug("started {} process to form small flow".format(self.generator_id))
		self.cv.acquire()

		# medium,large ?????????20%???50%??????????????????????????????,??????????????????2?????????

		traffic_scale_idx = 0
		while True:
			scale = self.traffic_scales[traffic_scale_idx]
			duration = self.durations[traffic_scale_idx]

			target_n_host = -1
			n_host = len(self.hostids)
			if scale == "small":
				target_n_host = 0
			elif scale == "medium":
				target_n_host = math.ceil(n_host * 0.2)
			elif scale == "large":
				target_n_host = math.ceil(n_host * 0.5)
			else:
				err("Invalid traffic scale {}".format(scale))

			# ???????????????????????????15????????????
			target_n_process = target_n_host * 15

			# we need to add more generator process
			# ? ?????????????????????????????????????????????????
			if target_n_process > len(self.schedule_record):
				n_add = target_n_process - len(self.schedule_record)
				# sample host
				sampled_hosts = random.sample(self.hostids, n_add // 15)
				for hid in sampled_hosts:
					for _ in range(15):
						self._start_traffic(hid, "video", True)

			# we need to reduce generator
			elif target_n_process < len(self.schedule_record):
				to_be_killed = self.schedule_record[target_n_process:]
				for pid in to_be_killed:
					self._stop_traffic(pid, "video", True)

			debug(self.schedule_record)

			if not self.cv.wait(duration):
				traffic_scale_idx = (traffic_scale_idx + 1) % (len(self.durations))
				debug("traffic mode changed")
				continue
			else:
				debug("Exit traffic scheduler")
				self.cv.release()
				for pid in self.pid2genid.keys():
					kill_pid(pid)
				self.genid2pid = {}
				self.pid2genid = {}
				self.processes = {
					"iot": [],
					"video": [],
					"voip": [],
				}
				self.schedule_record = []
				break

	def _do_stop_traffic_schedule(self):
		self.cv.acquire()
		self.cv.notify()
		self.cv.release()


if __name__ == '__main__':
	config_fn = "/home/stack/code/simulation/topo/distributed/mock_config.json"
	worker_id = 0
	intf = "eno1"
	from topo.distributed.topobuilder import TopoBuilder
	from utils.file_utils import load_json

	builder = TopoBuilder(load_json(config_fn), worker_id, intf)
	topo_fn = "/home/stack/code/simulation/topo/distributed/demo.topo.json"
	builder.diff_topo(load_json(topo_fn)["topo"])
	import time

	time.sleep(10)

	builder.start_gen_traffic_use_scheduler()


	def sigint_handler(signum, framezize):
		builder.stop_traffic_use_scheduler()


	signal.signal(signal.SIGINT, sigint_handler)
