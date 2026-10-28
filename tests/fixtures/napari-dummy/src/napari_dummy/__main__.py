"""`python -m napari_dummy fetch-model` saves a small tensor into the environment, as a model download would"""

import sys
from pathlib import Path

if sys.argv[1:] != ['fetch-model']:
    sys.exit('usage: python -m napari_dummy fetch-model')

import torch

path = Path(sys.prefix) / 'napari-dummy-model.pt'
torch.save(torch.zeros(4), path)
print(f'saved {path}')
