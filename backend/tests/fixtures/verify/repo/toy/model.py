import torch.nn as nn

from toy.attention import MultiHeadAttention

DROPOUT = 0.3
D_MODEL = 512
NUM_HEADS = 8
D_K = D_MODEL // NUM_HEADS


class SublayerConnection(nn.Module):
    def __init__(self, d_model):
        super().__init__()
        self.norm = nn.LayerNorm(d_model)
        self.drop = nn.Dropout(DROPOUT)

    def forward(self, x, sublayer):
        return self.norm(x + self.drop(sublayer(x)))


class EncoderLayer(nn.Module):
    def __init__(self):
        super().__init__()
        self.attn = MultiHeadAttention(D_MODEL, NUM_HEADS)
        self.sub = SublayerConnection(D_MODEL)

    def forward(self, x, mask=None):
        return self.sub(x, lambda y: self.attn(y, mask))


class DecoderLayer(EncoderLayer):
    pass


class Encoder(nn.Module):
    def __init__(self, n_layers=6):
        super().__init__()
        self.layers = nn.ModuleList([EncoderLayer() for _ in range(n_layers)])

    def forward(self, x, mask=None):
        for layer in self.layers:
            x = layer(x, mask)
        return x


class Decoder(nn.Module):
    def __init__(self):
        super().__init__()
        self.layers = nn.ModuleList([DecoderLayer() for _ in range(4)])

    def forward(self, x, mask=None):
        for layer in self.layers:
            x = layer(x, mask)
        return x
