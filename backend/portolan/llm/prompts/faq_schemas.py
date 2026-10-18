"""Structured outputs for the FAQ prompts (spec §10.10)."""

from __future__ import annotations

from pydantic import Field

from portolan.llm.prompts.lenient import ReplySchema


class ClusterAnswerOut(ReplySchema):
    cluster: int  # the `index` of the input cluster
    keep: bool = True  # false when neither the source nor the passages let you answer
    question: str  # the question, cleaned up into one standalone sentence
    answer: str  # 40-120 words, paraphrased
    anchors: list[str] = Field(default_factory=list[str])  # ids of the given passages it rests on


class AnswerBatchOut(ReplySchema):
    items: list[ClusterAnswerOut]


class AnticipatedOut(ReplySchema):
    concept: int  # the `index` of the input concept
    question: str  # what a newcomer would most plausibly ask about it
    answer: str
    anchors: list[str] = Field(default_factory=list[str])


class AnticipatedBatchOut(ReplySchema):
    items: list[AnticipatedOut]


class AskOut(ReplySchema):
    supported: bool  # false when the sources don't answer the question
    answer: str = ""  # 40-150 words; empty when unsupported
    anchors: list[str] = Field(default_factory=list[str])  # given passage ids
    faq: list[str] = Field(default_factory=list[str])  # given FAQ item ids
    code: list[str] = Field(default_factory=list[str])  # "path:start-end", as given
