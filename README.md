# Pain points

## supervisor
1. thread context management: summarize old turns' conversation(topic-detail pairs), keep recent turns' conversation, scrolling
2. user query analysis: decomposite, resolve each one, compose
3. citation: LLM tag during answer, parse

## search
1. evaluation recall/precision: only semantic, only keyword, both, how to fuse, rerank or not

## dataprep
1. which file format/content -> which extractor
2. whether to chunk, how to chunk
3. how to resolve images in the docs
4. when to fallback to raw file analysis by mutimodal LLM: token limitation, price, and efficiency