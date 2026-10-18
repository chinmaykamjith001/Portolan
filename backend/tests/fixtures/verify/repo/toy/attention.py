import torch
import torch.nn as nn


def attention(q, k, v, mask=None):
    """Scaled dot-product attention."""
    d_k = q.size(-1)
    scores = torch.matmul(q, k.transpose(-2, -1)) / d_k
    if mask is not None:
        scores = scores.masked_fill(mask == 0, -1e9)
    weights = torch.softmax(scores, dim=-1)
    return torch.matmul(weights, v)


class MultiHeadAttention(nn.Module):
    def __init__(self, d_model=512, num_heads=8):
        super().__init__()
        self.h = num_heads
        self.proj = nn.Linear(d_model, d_model)

    def forward(self, x, mask=None):
        return attention(self.proj(x), x, x, mask)
