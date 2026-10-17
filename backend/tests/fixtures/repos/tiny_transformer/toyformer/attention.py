"""Attention layers for the toy transformer (synthetic fixture written for tests)."""

import math

import torch
import torch.nn.functional as F
from torch import nn


def scaled_dot_product_attention(query, key, value, mask=None):
    """Score queries against keys, scale by sqrt(d_k), softmax, and weight the values."""
    d_k = query.size(-1)
    scores = torch.matmul(query, key.transpose(-2, -1)) / math.sqrt(d_k)
    if mask is not None:
        scores = scores.masked_fill(mask == 0, -1e9)
    weights = F.softmax(scores, dim=-1)
    return torch.matmul(weights, value), weights


class MultiHeadAttention(nn.Module):
    """Project into h heads, attend in parallel, then concatenate and project back."""

    def __init__(self, d_model: int, num_heads: int, dropout: float = 0.1):
        super().__init__()
        assert d_model % num_heads == 0
        self.d_k = d_model // num_heads
        self.num_heads = num_heads
        self.linears = nn.ModuleList([nn.Linear(d_model, d_model) for _ in range(4)])
        self.dropout = nn.Dropout(dropout)

    def forward(self, query, key, value, mask=None):
        batch = query.size(0)
        query, key, value = [
            lin(x).view(batch, -1, self.num_heads, self.d_k).transpose(1, 2)
            for lin, x in zip(self.linears, (query, key, value))
        ]
        x, _ = scaled_dot_product_attention(query, key, value, mask)
        x = x.transpose(1, 2).contiguous().view(batch, -1, self.num_heads * self.d_k)
        return self.linears[-1](x)
