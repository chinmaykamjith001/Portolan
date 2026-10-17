"""Training entry point: label smoothing loss, warmup schedule, CLI (synthetic fixture)."""

import argparse

import torch
from torch import nn

WARMUP_STEPS = 4000


class LabelSmoothingLoss(nn.Module):
    """KL divergence against a smoothed target distribution (epsilon spread over the vocabulary)."""

    def __init__(self, vocab_size: int, smoothing: float = 0.1):
        super().__init__()
        self.criterion = nn.KLDivLoss(reduction="sum")
        self.confidence = 1.0 - smoothing
        self.smoothing = smoothing
        self.vocab_size = vocab_size

    def forward(self, logits, target):
        true_dist = torch.full_like(logits, self.smoothing / (self.vocab_size - 1))
        true_dist.scatter_(1, target.unsqueeze(1), self.confidence)
        return self.criterion(logits.log_softmax(-1), true_dist)


def rate(step: int, d_model: int, warmup: int = WARMUP_STEPS) -> float:
    """Learning-rate warmup: grow linearly, then decay with the inverse square root of the step."""
    step = max(step, 1)
    return d_model**-0.5 * min(step**-0.5, step * warmup**-1.5)


def parse_args():
    parser = argparse.ArgumentParser(description="Train the toy transformer.")
    parser.add_argument("--d-model", type=int, default=512)
    parser.add_argument("--label-smoothing", type=float, default=0.0)
    parser.add_argument("--warmup", type=int, default=WARMUP_STEPS)
    return parser.parse_args()


if __name__ == "__main__":
    print(parse_args())
