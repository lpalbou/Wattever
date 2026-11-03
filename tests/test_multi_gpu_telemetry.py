"""Independent device readings, time-aligned totals and exact energy accounting."""
import io
import csv
import json
import unittest
from unittest.mock import patch

from wattever import power, store, runner

SENSORS=[{'index':0,'name':'RTX 5070 Ti','vram_total_mb':16000},
         {'index':1,'name':'RTX 5060 Ti','vram_total_mb':16000}]


class MultiGpuTests(unittest.TestCase):
    def sampler(self):
        sampler=power.PowerSampler(interval_ms=1000,other_idle_w=60,cpu_tdp_w=140)
        sampler.source=power.SRC_NVIDIA
        sampler._started_perf=100
        sampler._ended_perf=103
        sampler._configure_gpus(SENSORS)
        return sampler

    def append(self,sampler,t,watts0,watts1,mem0=1000,mem1=2000):
        rows={0:(t,watts0,40,mem0,2500,60,None,None),
              1:(t,watts1,80,mem1,2300,70,None,None)}
        row=power.PowerSampler._combined_gpu_row(t,rows)
        sampler._append(row[:6]+(.5,None),gpu_rows=rows)

    def test_two_card_energy_sums_and_cpu_is_added_once(self):
        sampler=self.sampler()
        for t in (100,101,102,103):self.append(sampler,t,100,50)
        now=sampler.now()
        self.assertEqual(now['gpu_w'],150)
        self.assertEqual(now['system_w_est'],280) # 150+60+140*0.5, once.
        self.assertEqual(now['gpu_util'],60) # mean, never 120%.
        self.assertEqual(now['vram_mb'],3000)
        self.assertEqual(now['vram_total_mb'],32000)
        self.assertEqual(now['temp_c'],70)
        self.assertIsNone(now['clock_mhz'])
        self.assertEqual([g['gpu_w'] for g in now['gpus']],[100,50])
        result=sampler.window(100,103)
        self.assertEqual(result['gpu_energy_j'],450)
        self.assertEqual(result['system_energy_j_est'],840)
        self.assertEqual(sum(g['gpu_energy_j'] for g in result['gpus']),450)
        self.assertTrue(result['gpu_power_complete'])
        summary=sampler.summary()
        self.assertEqual(len(summary['gpus']),2)
        self.assertEqual(summary['gpus'][1]['metrics']['gpu_w']['mean'],50)
        self.assertEqual(sampler.history()['scope'],'benchmark_client_all_nvidia_gpus')

    def test_peak_of_simultaneous_sum_is_not_sum_of_independent_peaks(self):
        sampler=self.sampler()
        self.append(sampler,100,100,200,1000,5000)
        self.append(sampler,101,200,100,5000,1000)
        result=sampler.window(100,101)
        self.assertEqual(result['gpu_w_max'],300)
        self.assertEqual(result['vram_mb_max'],6000)
        self.assertEqual(sum(g['vram_mb_max'] for g in result['gpus']),10000)

    def test_missing_sensor_cannot_become_an_apparently_complete_total(self):
        sampler=self.sampler()
        self.append(sampler,100,100,50)
        self.append(sampler,101,100,None)
        self.assertIsNone(sampler.now()['gpu_w'])
        self.assertFalse(sampler.now()['gpu_power_complete'])
        result=sampler.window(100,101)
        self.assertIsNone(result['gpu_energy_j'])
        self.assertIsNone(result['system_energy_j_est'])
        self.assertFalse(result['gpu_power_complete'])
        self.assertIsNotNone(result['gpus'][0]['gpu_energy_j'])
        self.assertIsNone(result['gpus'][1]['gpu_energy_j'])

    def test_stale_card_and_reversed_device_arrival_order(self):
        sampler=self.sampler()
        sampler._record_sensor((100,50,80,2000,2300,70,None,None),gpu_index=1)
        sampler._record_sensor((103,100,40,1000,2500,60,None,None),gpu_index=0)
        rows=sampler._gpu_rows_at(105)
        self.assertIsNone(rows[1][power.GPU_W])
        self.assertEqual(rows[0][power.GPU_W],100)
        self.assertIsNone(sampler._combined_gpu_row(105,rows)[power.GPU_W])

    def test_one_stream_parses_index_and_keeps_device_identity(self):
        sampler=self.sampler()
        class Process:
            stdout=io.StringIO('1, 50, 80, 2000, 2300, 70\n0, 100, 40, 1000, 2500, 60\n')
        with patch.object(power,'popen',return_value=Process()) as popen:
            sampler._loop_nvidia()
        self.assertIn('--id=0,1',popen.call_args[0][0])
        self.assertEqual(sampler._sensor_rows[0][power.GPU_W],100)
        self.assertEqual(sampler._sensor_rows[1][power.GPU_W],50)

    def test_history_ring_retains_per_card_whole_run_peaks(self):
        sampler=self.sampler()
        for t in range(4000):self.append(sampler,100+t,999 if t==0 else 100,50)
        self.assertEqual(len(sampler.history()['points']),3600)
        self.assertEqual(sampler.summary()['gpus'][0]['metrics']['gpu_w']['max'],999)
        self.assertEqual(sampler.summary()['metrics']['gpu_w']['max'],1049)

    def test_real_runner_assembly_and_csv_preserve_each_card_and_combined_energy(self):
        sampler=self.sampler()
        for t in (100,100.5,101,101.5,102,102.5,103):self.append(sampler,t,100,50)
        bench=runner.BenchRunner();bench.sampler=sampler
        measured={'t_start':100,'t_first_token':101,'t_end':103,'prompt_tokens':512,'completion_tokens':11,
                  'decode_token_count':10,'reasoning_tokens':0,'ttft_s':1,'decode_s':2,'wall_s':3,
                  'prefill_tps':512,'decode_tps':5,'finish_reason':'stop','sample_text':'answer'}
        with patch.object(bench,'_measure_completion',return_value=measured), \
             patch.object(runner.corpus,'build_prompt',return_value=('test',{'nonce':'abc','corpus_wraps':0})):
            row=bench._one_test({'results':[]},dict(runner.DEFAULT_CONFIG),'model',512,0,1,{},'max',1536,0,4)
        self.assertEqual(row['status'],'ok',row)
        self.assertEqual(row['power']['prefill_energy_j'],150)
        self.assertEqual(row['power']['decode_energy_j'],300)
        self.assertEqual(row['power']['j_per_token_decode'],30)
        self.assertEqual(len(row['power']['gpus']),2)
        self.assertEqual(row['power']['gpus'][1]['j_per_token_decode'],10)
        exported=list(csv.DictReader(io.StringIO(store.to_csv([{'id':'fake','results':[row]}]))))[0]
        self.assertEqual(float(exported['gpu_0_decode_energy_j']),200)
        self.assertEqual(float(exported['gpu_1_decode_energy_j']),100)
        self.assertEqual(float(exported['decode_gpu_energy_j']),300)
        self.assertEqual(exported['power_boundary'],'benchmark_client_all_nvidia_gpus')
        self.assertEqual(len(json.loads(exported['per_gpu_power_json'])),2)
        from wattever.validation import validate_gpu_power
        self.assertEqual(validate_gpu_power(row['power']),[])
        row['power']['decode_energy_j']+=1
        self.assertTrue(validate_gpu_power(row['power']))
        row['power']['decode_energy_j']=None
        self.assertEqual(validate_gpu_power(row['power']),[])
