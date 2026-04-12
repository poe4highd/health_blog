import sys
sys.path.insert(0, '/opt/CosyVoice')
import torchaudio
import torch
from cosyvoice.cli.cosyvoice import CosyVoice3 as ModelClass

kwargs = {'load_trt': False}
model = ModelClass('/opt/CosyVoice/pretrained_models/Fun-CosyVoice3-0.5B', **kwargs)

text = '你好'
instruct = '用流利的中文播报以下文本。'
prompt_wav = torch.zeros(1, 16000)

generator = model.inference_instruct2(text, instruct, prompt_wav, stream=False)
for chunk in generator:
    pass
print("OK")
