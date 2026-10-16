import { AutoModelForSeq2SeqLM, Tensor, env } from '@huggingface/transformers';
import { createInterface } from 'node:readline';
import { resolve } from 'node:path';
import { ensureWeights } from './prepare.mjs';

env.cacheDir = resolve(import.meta.dirname, '../data/translation-models');
if (process.env.STOCK_RESOURCE_ROOT) env.allowRemoteModels = false;
await ensureWeights(env.cacheDir);
env.backends.onnx.wasm.numThreads = 2;
const model = await AutoModelForSeq2SeqLM.from_pretrained('Xenova/opus-mt-en-zh', {
  dtype: 'q8', session_options: { intraOpNumThreads: 2, interOpNumThreads: 1 },
});
process.stdout.write(JSON.stringify({ready:true}) + '\n');
const lines = createInterface({input:process.stdin, crlfDelay:Infinity});
for await (const line of lines) {
  try {
    const request = JSON.parse(line);
    if (!Array.isArray(request.input_ids) || !request.input_ids.length || request.input_ids.length > 512
      || request.input_ids.some(id => !Number.isInteger(id) || id < 0 || id > 65000)) {
      throw new Error('Invalid translation input');
    }
    const input_ids = new Tensor('int64', BigInt64Array.from(request.input_ids, BigInt), [1, request.input_ids.length]);
    const attention_mask = new Tensor('int64', BigInt64Array.from(request.input_ids, () => 1n), [1, request.input_ids.length]);
    const result = await model.generate({input_ids, attention_mask, max_new_tokens:192, num_beams:4, no_repeat_ngram_size:4});
    process.stdout.write(JSON.stringify({id:request.id, output_ids:result.tolist()[0].map(Number)}) + '\n');
  } catch (error) {
    process.stdout.write(JSON.stringify({error:String(error.message || error)}) + '\n');
  }
}
