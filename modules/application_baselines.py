"""Source-backed dispute patterns, not a case database or automatic grounds for refusal."""
from copy import deepcopy

LAW = "https://www.cnipa.gov.cn/art/2020/11/23/art_524_171347.html"
ANNUAL = "https://www.cnipa.gov.cn/art/2026/4/26/art_3668_18.html"
TOPICS = {"general": "通用披露与申请边界", "mechanical": "机械部件、连接与传动",
          "parameters": "参数与测量", "chemical": "组合物、药物与实验效果", "ai": "算法与模型应用"}
PATTERNS = [
    {"id": "whole_document", "topic": "general", "grounds": ["新颖性"],
     "problem": "局部引文看似公开特征，但与文献其他内容矛盾。",
     "baseline": "完整阅读相关语境，能够确定实际披露的方案；不能只按局部词句认定完整公开。",
     "context": "争议段落、相邻定义、矛盾内容及完整目标限定。",
     "source": {"title": "电泳漆添加剂案；(2021)最高法知行终83号", "url": "https://ipc.court.gov.cn/zh-cn/news/view-3322.html"},
     "limit": "原驳回及被诉决定被撤销重作；不能把历史驳回当最终新颖性否定，也不能写成已经授权。"},
    {"id": "separate_grounds", "topic": "general", "grounds": ["新颖性", "创造性", "公开充分性", "支持", "程序"],
     "problem": "用未授权、无效或视为撤回的状态，替代具体技术及法律理由。",
     "baseline": "分别核验具体权利要求、版本和理由；未授权不自动排除其已公开文本作为现有技术。",
     "context": "实际文书、适用条款、阶段、文本版本和公开日期。",
     "source": {"title": "专利法第二十二、二十六、三十七条", "url": LAW},
     "limit": "状态是线索；缺少文书不能推定不过审原因。"},
    {"id": "llm_not_prior_art", "topic": "general", "grounds": ["现有技术证据", "公知常识证明"],
     "problem": "用当前大模型生成内容证明申请日前技术状态或本领域认知。",
     "baseline": "回到可追溯且具有日期依据的原始资料；模型解释不能自行成为证据。",
     "context": "原始出处、公开时间、实际技术内容与引用用途。",
     "source": {"title": "钨锆合金金相制备方法复审案；1F490428", "url": ANNUAL},
     "limit": "官方摘要未列完整驳回理由；此项只约束证据使用。"},
    {"id": "mechanism_not_same_word", "topic": "mechanical", "grounds": ["技术特征认定", "创造性中的披露事实"],
     "problem": "将余量、间隙等近似表达脱离部件运动和接触关系直接等同。",
     "baseline": "核验几何方向、接触状态、运动自由度及作用；同一目的不证明相同结构。",
     "context": "完整关系、工作状态、部件说明及必要附图；附图不可用须说明。",
     "source": {"title": "创造性评判中如何判断对比文件所公开的特征", "url": "https://www.cnipa.gov.cn/art/2022/7/27/art_2650_176869.html"},
     "limit": "这是创造性案例中的语义解释参考，不直接标作新颖性失败。"},
    {"id": "parameter_reproduction", "topic": "parameters", "grounds": ["披露事实", "证据可靠性"],
     "problem": "以软件复现的参数直接认定现有技术披露，却未核验计算条件和工具可靠性。",
     "baseline": "保留参数定义、单位、输入、方法及复现依据，不能由数值相近直接推定同一披露。",
     "context": "原始文献、参数定义、软件来源和功能、计算设置及适配说明。",
     "source": {"title": "光学成像系统无效案；4W117759", "url": ANNUAL},
     "limit": "全部无效是该案结果；不据摘要推定任意参数争议必然不过审。"},
    {"id": "effect_and_data", "topic": "chemical", "grounds": ["创造性", "实验数据证明力"],
     "problem": "忽略组分比例、试验条件和比较对象，直接采信或否定协同效果。",
     "baseline": "效果解释应绑定具体方案和数据的证明范围；补交数据适用性另行核验。",
     "context": "组分、比例、实施例、对照、试验条件、原始公开及补交数据用途。",
     "source": {"title": "药物组合物4W119517、C-MET/HGFR抑制剂多晶型物4W118141", "url": ANNUAL},
     "limit": "两案摘要均记载维持有效；用于防止错误否定效果，不将创造性推理混入新颖性。"},
    {"id": "model_application_context", "topic": "ai", "grounds": ["创造性", "技术方案解释"],
     "problem": "仅按模型名称或应用场景认定相同方案，忽略训练方法与实际处理关系。",
     "baseline": "明确应用场景、训练/推理、输入处理输出及技术作用；是否有创造性应另行评述。",
     "context": "模型用途、训练方法、技术数据流及原文支持；不能由模型名补造实现。",
     "source": {"title": "基于音频生成动态图像案；4W118569", "url": ANNUAL},
     "limit": "该案维持有效；不推广为更换场景或模型即具备新颖性/创造性。"},
]


def select_baselines(topics):
    if not isinstance(topics, list) or any(not isinstance(t,str) or t not in TOPICS for t in topics):
        raise ValueError("申请底线主题无效。")
    selected = set(topics) | {"general"}
    return {"purpose": "同类技术的共性争议与申请检查底线，非失败频率或自动拒绝规则。",
            "patterns": deepcopy([p for p in PATTERNS if p["topic"] in selected]),
            "application": "先比对争议关系、限定及证据条件是否相似；只有当前原文支持时才能指出具体问题。"}
