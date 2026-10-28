"""导出可复核报告：保留逐篇对照、时间基准、来源类型和原文快照哈希。"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from urllib.parse import quote as url_quote


def build_payload(state: dict, invention: str, cutoff: str, lexicon_groups: list) -> dict:
    from modules.patent_context import context_for
    from modules.application_baselines import select_baselines
    ctx=context_for(state["features"],cutoff,state.get("patent_context"),invention=invention)
    target=ctx["target"]
    scope=(f"Google Patents；版本{target['version_id']}；权利要求{target['claim_id']}；引用路径{' → '.join(target['path'])}"
           if target.get("claim_id") else "Google Patents；整套用户确认要素")
    payload = {
        "report_schema_version": 3,
        "created_at": datetime.now(timezone.utc).isoformat(), "task": "CN_novelty_precheck",
        "scope": scope, "patent_context":ctx,
        "application_baselines":select_baselines(ctx["baseline_topics"]),
        "cutoff": cutoff, "invention": invention,
        "features": [asdict(f) for f in state["features"]],
        "documents": [doc.to_dict() | {"snapshot_hash": doc.snapshot_hash} for doc in state["documents"]],
        "reviews": [asdict(review) for review in state.get("reviews", [])],
        "candidate_reviews": [asdict(review) for review in state.get("suggestions", [])],
        "evidence_alignment": {"primary": "model_semantics", "retrieval": "reading_hints_only",
                               "coverage": "all_snapshot_sections"},
        "retrieval_hints": state.get("hits", []),
        "decision": asdict(state["decision"]), "trace": state["trace"], "synonym_groups": lexicon_groups,
        "material_unknowns": state.get("material_unknowns", []),
        "material_warnings": state.get("material_warnings", []),
        "search_audit": state.get("search_audit", {"mode": "not_recorded", "queries": [],
            "scope_note": "历史记录未保存实际查询，不能推定执行过在线检索。"}),
        "retrieval_increment": state.get("retrieval_increment"),
        "limits": ["未发现完整披露不等于可以授权。", "同义词匹配不证明技术披露或法律等同。",
                   "Google Patents 不包含全部非专利现有技术；日期、抵触申请、充分公开等仍可能需要专业核验。",
                   "模型逐项复核不等于人工确认，不能作为真实专业准确率的金标准。"],
    }
    if state.get("claim_history"):
        payload["claim_history"]=state["claim_history"]
    return payload


def to_markdown(payload: dict, *, include_skill_audit=True) -> str:
    from modules.novelty_models import LABELS

    lines = ["# 新颖性预筛报告", "", f"结论：**{LABELS[payload['decision']['label']]}**",
             f"判断基准日：{payload['cutoff']}", f"范围：{payload['scope']}",
             "", "## 发明描述", payload["invention"], "", "## 已确认技术要素"]
    lines += [f"- {f['id']}：{f['text']}" for f in payload["features"]]
    target=payload.get("patent_context",{}).get("target",{})
    if target.get("claim_id"):
        lines += ["", "## 权利要求及修改范围", target["boundary"]]
        lines += [f"- 权利要求{c['id']}（{c['kind']}）：{c['text']}" for c in target["claims"]]
        for change in target["amendments"]:
            before=(change["before"] or {}).get("text","本版本未记载")
            after=(change["after"] or {}).get("text","本版本已删除")
            lines += [f"- {change['version_id']} / {change['claim_id']}：{before} → {after}",
                      "  修改依据：" + ("；".join(b["explanation"] for b in change["basis"]) or "未提供，不能确认修改支持或合法性。")]
    if payload.get("application_baselines"):
        lines += ["", "## 同类技术争议与申请底线", "以下为有出处的核查依据；须与本案具体限定及证据比对，不据此自动认定不过审。"]
        for pattern in payload["application_baselines"]["patterns"]:
            lines += [f"- **{pattern['problem']}**（{'、'.join(pattern['grounds'])}）：{pattern['baseline']}",
                      f"  适用边界：{pattern['limit']} [来源]({pattern['source']['url']})"]
    if payload.get("evidence_alignment"):
        lines += ["", "证据对照以模型对技术含义及全部限定的理解为主；LlamaIndex提供本地阅读线索。"
                  "模型须检查全部输入原文，可以采用未命中段落，逐项说明支持关系、矛盾或不确定性。"]
    audit = payload.get("search_audit", {"mode": "not_recorded"})
    lines += ["", "## 检索范围与实际记录"]
    if audit["mode"] != "online":
        lines += [audit.get("scope_note", "仅比对提供的快照；历史实际查询未记录。")]
    else:
        lines += [f"模式：{audit.get('query_mode')}；查询预算：{audit['query_budget']}；每条最多保留{audit['results_per_query']}条返回记录；"
                  f"正文获取预算：{audit['document_budget']}",
                  f"开始：{audit['started_at']}；结束：{audit['finished_at']}",
                  "要素编号表示该要素的查询已发送，不证明该查询足以覆盖所有技术表达。词汇重合分数仅用于排序，不代表相关性。"]
        for record in audit["queries"]:
            lines += [f"- {record['id']} · 要素={', '.join(record['feature_ids']) or '自定义范围未确认'}；{record['kind']}；"
                      f"{record['status']}；{record['started_at']} → {record['finished_at']}；返回{record['returned_count']}条",
                      f"  实际查询：{record['query']}"]
            if record["error"]:
                lines += [f"  错误：{record['error']}"]
            for result in record["results"]:
                lines += [f"  候选：{result.get('publication_id', '未通过来源校验')} · {result.get('title', '')} · "
                          f"{result.get('url', '')}；{result.get('validation_error', '')}"]
        for skipped in audit.get("skipped", []):
            lines += [f"- 未发送 · {', '.join(skipped['feature_ids']) or '自定义变体'}：{skipped['reason']}；查询原文：{skipped['query']}"]
        for candidate in audit["candidates"]:
            lines += [f"- 正文获取 · {candidate['publication_id']}：{candidate['fetch_status']}；{candidate['fetch_error']}；"
                      f"来自{', '.join(candidate['query_ids'])}；词汇排序提示={candidate['lexical_hint']['score']}"]
        fetched_ids = {c["publication_id"] for c in audit["candidates"] if c["fetch_status"] == "fetched"}
        supplied_ids = [doc["id"] for doc in payload["documents"] if doc["id"] not in fetched_ids
                        or doc["source_kind"] == "manual_google_patents_snapshot"]
        if supplied_ids:
            lines += ["另行提供的快照（不冒充在线获取）：" + "、".join(supplied_ids)]
    expansion = audit.get("expansion")
    if expansion:
        lines += ["", "## 同义词四层扩展与成本", f"查询策略：{expansion['policy']['label']}；"
                  f"最多{expansion['policy']['query_budget']}条查询；保护术语：{', '.join(expansion['protected_terms']) or '未指定'}",
                  "LLM新增术语仅用于查询，不写入证据嵌入词典或法律等同判断。"]
        for item in expansion["candidates"]:
            lines += [f"- {item['id']} · {item['feature_id']} · {item['source']} → {item['term']}；"
                      f"来源={item['origin']}；{item['status']}；{item['reason']}"]
        cost = audit.get("retrieval_cost", {})
        lines += [f"实际检索调用：{cost.get('search_calls', '未记录')}；正文尝试：{cost.get('page_attempts', '未记录')}；"
                  f"独立候选：{cost.get('unique_candidates', '未记录')}；检索耗时秒：{cost.get('elapsed_seconds', '未记录')}",
                  f"候选生成状态：{expansion['generation']['status']}；生成成本：{expansion['generation'].get('cost', {})}",
                  "扩展质量需独立语义eval核验术语保真、证据价值与漂移；本轮在线查询本身不证明质量改善。"]
    ranking = audit.get("ranking")
    if ranking:
        lines += ["", "## 候选排序与相关性层级", f"排序器：{ranking.get('version')}；模式：{ranking.get('mode')}；"
                  f"状态：{ranking.get('status', 'rules')}",
                  "规则与模型元数据判断只用于优先读取；unknown不排除，相关性不等于全文披露。"]
        for candidate in audit.get("candidates", []):
            hint = candidate.get("ranking_hint", {})
            metadata = candidate.get("metadata_relevance") or {}
            lines += [f"- {candidate['publication_id']}：词汇排序={candidate.get('legacy_lexical_rank', '未记录')}；"
                f"当前排序={candidate.get('rank', '未记录')}；规则分={hint.get('score', '未记录')}；"
                f"仅提及线索={hint.get('mention_only_hint', '未记录')}；来源={candidate.get('retrieval_origin', 'unknown')}；"
                f"模型元数据判断={metadata.get('relevance', 'unknown')}；{metadata.get('basis', '')}"]
            for check in metadata.get("feature_checks", []):
                lines += [f"  {check['feature_id']} · {check['match']} · {check['context_id']}/{check['field']}：{check['quote']}"]
        for index, run in enumerate(ranking.get("history", []), 1):
            lines += [f"- 元数据判断第{index}次：{run['status']}；成本={run.get('cost', {})}；{run.get('error', '')}"]
    increment = payload.get("retrieval_increment")
    if increment and increment.get("rows"):
        lines += ["", "## 扩展新增文献及正文相关性", increment["scope"],
            f"原词查询完成：{increment['baseline_complete']}；扩展独有：{', '.join(increment['expansion_only_ids']) or '无'}；"
            f"正文复核判为相关：{', '.join(increment['observed_new_body_related_ids']) or '尚无'}；"
            f"可归因于扩展的正文相关文献：{', '.join(increment['attributable_new_body_related_ids']) or '尚无'}",
            f"额外查询：{increment['extra_search_queries']}；每篇新增正文相关文献的额外查询数："
            f"{increment['extra_queries_per_observed_body_related'] if increment['extra_queries_per_observed_body_related'] is not None else '不可计算'}"]
        for row in increment["rows"]:
            body = row.get("body_review") or {}
            lines += [f"- {row['publication_id']}：扩展独有={row['expansion_only_observed']}；增量归因={row['attributable_increment']}；"
                f"元数据={row['metadata_relevance']}；正文={row['body_relevance']}；正文复核来源={body.get('source', '未复核')}；"
                f"依据={body.get('basis', '尚无')}；位置={', '.join(body.get('section_ids', [])) or '尚无'}"]
        lines += [f"- {value}" for value in increment["limits"]]
    lines += ["", "## 判断依据"] + [f"- {reason}" for reason in payload["decision"]["reasons"]]
    lines += ["", "## 缺口与待核验事项"] + [f"- {warning}" for warning in payload["decision"]["warnings"]]
    reviews = {item["document_id"]: item for item in payload["reviews"]}
    for doc in payload["documents"]:
        review = reviews.get(doc["id"])
        lines += ["", f"## {doc['id']} · {doc['title']}", f"来源：{doc['url']}",
                  f"公开日：{doc['publication_date'] or '未取得'}；申请日：{doc['filing_date'] or '未取得'}；"
                  f"优先权日：{doc['priority_date'] or '未取得'}",
                  f"资料类型：{doc['source_kind']}；快照格式：{doc.get('snapshot_schema_version', 1)}",
                  (f"原始完整性标记：{doc['complete']}；解析结构提示：{doc.get('parsed_complete', False)}"
                   if doc.get('snapshot_schema_version') == 2 else
                   f"历史完整性标记：{doc['complete']}（不自动视为新的人工确认）"),
                  f"快照 SHA-256：`{doc['snapshot_hash']}`"]
        sections = {section['id']: section for section in doc['sections']}
        if review:
            is_model = review.get("review_method", "human") == "model"
            lines += [f"复核方式：{'大模型逐项复核（非人工确认）' if is_model else '历史/人工复核'}"]
            if is_model:
                lines += [f"模型：{review.get('review_model') or '未记录'}；服务：{review.get('review_endpoint') or '未记录'}；"
                          f"提示词版本：{review.get('review_prompt_version') or '未记录'}",
                          f"模型上下文完整性判断：{review.get('context_complete', False)}；"
                          f"说明：{review.get('completeness_basis') or '未提供'}；"
                          f"已检查段落：{', '.join(review.get('reviewed_section_ids', [])) or '未记录'}"]
            lines += [f"提交复核标记：{review['reviewed']}；同一方案确认：{review['same_scheme_confirmed']}；"
                      f"充分公开确认：{review['enabling_disclosure_confirmed']}",
                      f"{'模型复核执行者' if is_model else '复核人（自填）'}：{review.get('reviewer') or '未记录'}；"
                      f"提交时间：{review.get('reviewed_at') or '未记录'}",
                      f"复核输入绑定：`{review.get('input_hash') or '历史记录未绑定，须重新复核'}`",
                      f"同一方案说明：{review.get('same_scheme_basis') or '未提供'}；"
                      f"依据位置：{', '.join(review.get('scheme_section_ids', [])) or '未提供'}",
                      f"技术相关性：{review.get('relevance', 'unknown')}；依据：{review.get('relevance_basis') or '未提供'}；"
                      f"原文位置：{', '.join(review.get('relevance_section_ids', [])) or '未提供'}"]
            for row in review["rows"]:
                section = sections.get(row['section_id'], {})
                location = section.get('source_location') or f"旧版快照内部编号 {row['section_id']}（非原文权利要求号）"
                anchor = section.get('source_anchor')
                link = doc['url'] + ('#' + url_quote(anchor, safe='') if anchor else '')
                origin = {'auto': '自动定位候选', 'model': '模型复核' if is_model else '模型候选',
                          'human': '人工填写'}.get(row.get('origin'), '历史来源未记录')
                lines += [f"- {row['feature_id']}：{row['status']}；原文位置=[{location}]({link})；内部ID={row['section_id']}；"
                          f"技术方案={row['scheme_id']}；引文来源={origin}；复核说明={row['basis'] or '未提供'}",
                          f"  引文：{row['quote'] or '未取得'}"]
    if payload.get('report_schema_version') in {2, 3}:
        lines += ["", "模型复核与人工复核按来源分别记录。历史人工身份为操作人自填，系统不核验姓名或专业资质；"
                  "候选建议另存于JSON，不等于最终复核。"]
    lines += ["", "## 执行记录", " → ".join(payload["trace"]), "", "## 适用边界"]
    lines += [f"- {limit}" for limit in payload["limits"]]
    if payload.get("material_case"):
        material = payload["material_case"]
        lines += ["", "## 原始材料案件", f"案件：{material['case_id']}",
                  f"材料清单 SHA-256：`{material['case_sha256']}`"]
        for attachment in material["attachments"]:
            lines += [f"- {attachment['name']}；SHA-256：`{attachment['sha256']}`"]
        preparation = payload.get("feature_preparation")
        if preparation:
            lines += ["", "AI 整理候选不等于人工复核；附件引文的语义支持仍须核查。"]
            if preparation.get("preparation_ref"):
                revision = preparation["preparation_ref"]
                lines += [f"整理版本：{revision['path']}；SHA-256：`{revision['sha256']}`"]
            if not payload.get("feature_preparation_matches_input", True):
                lines += ["当前要素已修改：以下出处属于原整理候选，不自动视为新要素的材料支持。"]
            for feature in preparation["features"]:
                for evidence in feature["evidence"]:
                    validation = {"verified_exact_text": "逐字引文已机器回查（不代表语义确认）",
                                  "not_machine_verified": "引文未机器校验"}.get(evidence.get("quote_validation"), "引文未机器校验")
                    lines += [f"- {feature['id']}：{evidence['source']} / {evidence['location']}；{validation}；原文：{evidence['quote']}"]
            lines += [f"- 材料未知项：{item}" for item in preparation["unknowns"]]
    if include_skill_audit and payload.get("skill_records"):
        lines += ["", "## 六个业务技能执行记录"]
        lines += [f"- {r['skill_name']}：{r['execution']}；来源：{r['producer']}；{r['note']}" for r in payload["skill_records"]]
    if include_skill_audit and payload.get("semantic_evaluation"):
        from modules.semantic_eval import evaluation_markdown
        lines += ["", evaluation_markdown(payload["semantic_evaluation"])]
    lines += ["", "独立语义评估按术语与对象、结构与关系、逻辑与限定、语境与指称、功能与效果、评价与表达核验；模型裁判不等于人工确认。"]
    return "\n\n".join(lines)
