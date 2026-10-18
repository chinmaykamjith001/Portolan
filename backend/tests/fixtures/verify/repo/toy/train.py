import argparse

import torch


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--epochs", type=int, default=10)
    args = parser.parse_args()
    model = torch.nn.Linear(4, 4)
    opt = torch.optim.Adam(model.parameters(), lr=args.lr)
    for _ in range(args.epochs):
        loss = model(torch.zeros(4)).sum()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        opt.step()


if __name__ == "__main__":
    main()
