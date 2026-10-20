#!/bin/bash
export CUDA_HOME=/usr/local/cuda PATH=/root/qwen-vllm-venv/bin:/usr/local/cuda/bin:$PATH LD_LIBRARY_PATH=/usr/local/cuda/lib64 TRITON_CACHE_DIR=/root/.triton/cache
exec /root/qwen-vllm-venv/bin/python3 -m vllm.entrypoints.openai.api_server   --model /root/sounding/models/Qwen3.5-9B-AWQ --host 127.0.0.1 --port 8011 --served-model-name sounding-qwen   --enforce-eager --enable-prefix-caching --gpu-memory-utilization 0.85   --max-model-len 32768 --max-num-seqs 2 --trust-remote-code
