"""Building blocks: positional encoding, feed-forward, residual + layer norm (synthetic fixture)."""

import math

import torch
from torch import nn

from .attention import MultiHeadAttention

MAX_LEN = 5000


class PositionalEncoding(nn.Module):
    """Add fixed sine and cosine signals so the model can tell token positions apart."""

    def __init__(self, d_model: int, dropout: float = 0.1, max_len: int = MAX_LEN):
        super().__init__()
        self.dropout = nn.Dropout(p=dropout)
        pe = torch.zeros(max_len, d_model)
        position = torch.arange(0, max_len).unsqueeze(1)
        div_term = torch.exp(torch.arange(0, d_model, 2) * -(math.log(10000.0) / d_model))
        pe[:, 0::2] = torch.sin(position * div_term)
        pe[:, 1::2] = torch.cos(position * div_term)
        self.register_buffer("pe", pe.unsqueeze(0))

    def forward(self, x):
        return self.dropout(x + self.pe[:, : x.size(1)])


class PositionwiseFeedForward(nn.Module):
    """Two linear layers with a ReLU in between, applied to every position."""

    def __init__(self, d_model: int, d_ff: int, dropout: float = 0.1):
        super().__init__()
        self.w_1 = nn.Linear(d_model, d_ff)
        self.w_2 = nn.Linear(d_ff, d_model)
        self.dropout = nn.Dropout(dropout)

    def forward(self, x):
        return self.w_2(self.dropout(self.w_1(x).relu()))


class SublayerConnection(nn.Module):
    """Residual connection followed by layer normalization."""

    def __init__(self, size: int, dropout: float):
        super().__init__()
        self.norm = nn.LayerNorm(size)
        self.dropout = nn.Dropout(dropout)

    def forward(self, x, sublayer):
        return x + self.dropout(sublayer(self.norm(x)))


class EncoderLayer(nn.Module):
    """Self-attention then feed-forward, each wrapped in a residual sublayer."""

    def __init__(self, d_model: int, num_heads: int, d_ff: int, dropout: float):
        super().__init__()
        self.self_attn = MultiHeadAttention(d_model, num_heads, dropout)
        self.feed_forward = PositionwiseFeedForward(d_model, d_ff, dropout)
        self.sublayers = nn.ModuleList([SublayerConnection(d_model, dropout) for _ in range(2)])

    def forward(self, x, mask):
        x = self.sublayers[0](x, lambda y: self.self_attn(y, y, y, mask))
        return self.sublayers[1](x, self.feed_forward)
