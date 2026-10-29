"""专利预筛主入口：为六个业务阶段配置执行依赖。"""
from modules.google_patent_documents import fetch_google_document
from modules.patent_search import search_patents
from modules.precheck_graph import PrecheckGraph
from modules.skill_contracts import SKILLS

BUSINESS_SKILLS = tuple(SKILLS)


class NoveltyHarness(PrecheckGraph):
    def __init__(self, *, searcher=None, fetcher=None, **kwargs):
        super().__init__(searcher=searcher or search_patents,
                         fetcher=fetcher or fetch_google_document, **kwargs)


def run(**kwargs):
    return NoveltyHarness().run(**kwargs)
