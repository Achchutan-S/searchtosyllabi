from syllabus_agent.pipeline.source_collection.collect import collect_sources
from syllabus_agent.schemas.query import SearchQuery


async def test_collect_sources_filters_to_trustworthy_and_attaches_trust_score(fake_search):
    queries = [SearchQuery(query="data structures syllabus")]

    result = await collect_sources("data structures", queries, fake_search)

    # The .edu hit should survive the trust filter, the example.com blog should not.
    assert len(result.sources) == 1
    source = result.sources[0]
    assert source.domain == "mit.edu"
    assert source.trust_score >= 0.5
    assert source.university == "mit.edu"
    assert fake_search.calls == ["data structures syllabus"]


async def test_collect_sources_dedupes_repeated_urls(fake_search):
    queries = [SearchQuery(query="q1"), SearchQuery(query="q2")]

    result = await collect_sources("data structures", queries, fake_search)

    urls = [str(s.url) for s in result.sources]
    assert len(urls) == len(set(urls))
