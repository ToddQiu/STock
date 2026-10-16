import { createHash } from 'node:crypto';
import { createReadStream } from 'node:fs';
import { mkdir, stat, unlink } from 'node:fs/promises';
import { resolve } from 'node:path';
import { spawnSync } from 'node:child_process';

const weights = [
  ['encoder_model_quantized.onnx', 52899742, 'd3b7912bf6a9bd27e4c074c2df91d4ff3d5b4bc5f7f6c8d7cc9c805c98fbafee'],
  ['decoder_model_merged_quantized.onnx', 60212804, '023be4f841f4c47cd65fffcbaa81c0d99d7f7e0138f7ba0e03fa220a4e688aff'],
];

async function checksum(path) {
  const hash = createHash('sha256');
  for await (const part of createReadStream(path)) hash.update(part);
  return hash.digest('hex');
}

export async function ensureWeights(cacheDir, {download = false} = {}) {
  const directory = resolve(cacheDir, 'Xenova/opus-mt-en-zh/onnx');
  await mkdir(directory, {recursive:true});
  for (const [name, size, digest] of weights) {
    const path = resolve(directory, name);
    const found = await stat(path).catch(() => null);
    if (found?.size === size && await checksum(path) === digest) continue;
    if (!download) throw new Error('Translation weights missing or incomplete; run npm run setup in translation_runtime');
    if (found?.size >= size) await unlink(path);
    const args = ['--silent', '--show-error', '--location', '--fail', '--retry', '2', '--retry-delay', '5',
      '--max-time', '300', '--continue-at', '-', '--output', path,
      `https://huggingface.co/Xenova/opus-mt-en-zh/resolve/main/onnx/${name}`];
    if (process.env.HTTPS_PROXY) args.push('--proxy', process.env.HTTPS_PROXY);
    const result = spawnSync(process.platform === 'win32' ? 'curl.exe' : 'curl', args,
      {windowsHide:true, stdio:['ignore', 'ignore', 'inherit'], timeout:920000});
    if (result.status !== 0 || (await stat(path)).size !== size || await checksum(path) !== digest) {
      throw new Error('Translation model download incomplete; will resume on retry');
    }
  }
}

if (process.argv.includes('--download')) {
  await ensureWeights(resolve(import.meta.dirname, '../data/translation-models'), {download:true});
}
