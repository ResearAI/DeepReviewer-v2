# Copyright (c) 2024 Bytedance Ltd. and/or its affiliates
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
from __future__ import annotations

from transformers import AutoModelForCausalLM, AutoTokenizer
import torch
import os

class Agent:
    def __init__(self, model_name):
        # Determine device for PASA model
        pasa_device = os.environ.get('PASA_CUDA_DEVICE', 'cuda:4')

        # Validate CUDA device
        if 'cuda' in pasa_device:
            try:
                device_id = int(pasa_device.split(':')[1]) if ':' in pasa_device else 0
                if device_id >= torch.cuda.device_count():
                    print(f"⚠️  PASA: GPU {device_id} not available (only {torch.cuda.device_count()} GPUs), using cuda:0")
                    pasa_device = 'cuda:0'
            except:
                pasa_device = 'cuda:0'

        try:
            # Load model (fixed: use dtype instead of torch_dtype to avoid deprecation warning)
            self.model = AutoModelForCausalLM.from_pretrained(
                model_name,
                dtype=torch.bfloat16,
            ).to(pasa_device)

            print(f"✅ PASA model loaded on {pasa_device}")

        except Exception as e:
            print(f"⚠️  PASA: Failed to load on {pasa_device}: {e}")
            print(f"   Falling back to CPU mode...")

            # Fall back to CPU
            self.model = AutoModelForCausalLM.from_pretrained(
                model_name,
                dtype=torch.bfloat16,
            ).to('cpu')
            pasa_device = 'cpu'
            print(f"✅ PASA model loaded on CPU (will be slower)")

        self.tokenizer = AutoTokenizer.from_pretrained(
            model_name,
            padding_side='left'
        )
    
    def infer_score(self, prompts):
        if len(prompts) == 0:
            return []
        encoded_input = self.tokenizer(prompts, return_tensors='pt', padding=True, truncation=True)
        input_ids = encoded_input.input_ids.cuda(self.model.device)
        attention_mask = encoded_input.attention_mask.cuda(self.model.device)

        outputs = self.model.generate(
            input_ids=input_ids,
            attention_mask=attention_mask,
            max_new_tokens=1,
            output_scores=True, 
            return_dict_in_generate=True, 
            do_sample=False
        )
        true_token_id = self.tokenizer.convert_tokens_to_ids('True')
        probs = outputs.scores[0].softmax(dim=-1)[:, true_token_id].cpu().numpy().tolist()
        return probs

    def infer(self, prompt, sample=False):
        text = self.tokenizer.apply_chat_template(
            [{
                "content": prompt.strip(),
                "role":    "user"
            }],
            tokenize=False,
            max_length=992,
            add_generation_prompt=True
        )
        model_inputs = self.tokenizer([text], return_tensors="pt").to(self.model.device)
        if sample:
            model_inputs["do_sample"] = True
            model_inputs["temperature"] = 2.0
            model_inputs["top_p"] = 0.8

        generated_ids = self.model.generate(
            **model_inputs,
            max_new_tokens=512
        )
        generated_ids = [
            output_ids[len(input_ids):] for input_ids, output_ids in zip(model_inputs.input_ids, generated_ids)
        ]

        response = self.tokenizer.batch_decode(generated_ids, skip_special_tokens=True)[0]
        return response
    
    def batch_infer(self, prompts, batch_size=8, sample=False):
        if len(prompts) == 0:
            return []
        texts = [self.tokenizer.apply_chat_template(
            [{
                "content": prompt.strip(),
                "role":    "user"
            }],
            tokenize=False,
            max_length=992,
            add_generation_prompt=True
        ) for prompt in prompts]
        responses = []
        for i in range(0, len(texts), batch_size):
            model_inputs = self.tokenizer(texts[i: i + batch_size], return_tensors="pt", truncation=True, padding=True).to(self.model.device)
            if sample:
                model_inputs["do_sample"] = True
                model_inputs["temperature"] = 2.0
                model_inputs["top_p"] = 0.8
            generated_ids = self.model.generate(
                **model_inputs,
                max_new_tokens=512
            )
            generated_ids = [
                output_ids[len(input_ids):] for input_ids, output_ids in zip(model_inputs.input_ids, generated_ids)
            ]
            for response in self.tokenizer.batch_decode(generated_ids, skip_special_tokens=True):
                responses.append(response)
        return responses
    
if __name__ == "__main__":
    selector = Agent("/mnt/hdfs/foundation/agent/heyc/checkpoints/pasa-7b-selector")
    promtp = "You are an elite researcher in the field of AI, conducting research on Give me papers which shows that using a smaller dataset in large language model pre-training can result in better models than using bigger datasets.\n. Evaluate whether the following paper fully satisfies the detailed requirements of the user query and provide your reasoning. Ensure that your decision and reasoning are consistent.\n\nSearched Paper:\nTitle: Specialized Language Models with Cheap Inference from Limited Domain Data\nAbstract:  Abstract Large language models have emerged as a versatile tool but are challenging to apply to tasks lacking large inference budgets and large in-domain training sets. This work formalizes these constraints and distinguishes four important variables: the pretraining budget (for training before the target domain is known), the specialization budget (for training after the target domain is known), the inference budget, and the in-domain training set size. Across these settings, we compare different approaches from the machine learning literature. Limited by inference cost, we find better alternatives to the standard practice of training very large vanilla transformer models. In particular, we show that hyper-networks and mixture of experts have better perplexity for large pretraining budgets, while small models trained on importance sampled datasets are attractive for large specialization budgets. \n\nUser Query: Give me papers which shows that using a smaller dataset in large language model pre-training can result in better models than using bigger datasets.\n\n\nOutput format: Decision: True/False\nReason:... \nDecision:"
    print(selector.infer_score([promtp, promtp, promtp]))
