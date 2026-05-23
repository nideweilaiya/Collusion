# {{ task }}

> 生成时间：{{ generated_at }} | 总成本：¥{{ "%.4f"|format(cost) }} | Token：{{ tokens }} | Agent 数量：{{ agents }}

---

{% if top_scheme %}
## 🏆 推荐方案：{{ top_scheme.id }} — {{ top_scheme.object_name }}

**综合得分：{{ "%.2f"|format(top_scheme.total_score) }}/10 | 排名：#1**

**评委评语：** {{ top_scheme.comment }}

### 完整技术方案

{{ top_scheme.integrated_content }}

{% endif %}

---

## 📊 备选方案对比

| 维度 | 方案 A | 方案 B | 方案 C |
|:---|:---|:---|:---|
{% for dim in dimensions %}
| {{ dim }} |{% for scheme in schemes %}{% set score = scheme.scores.get(dim, "-") %}{% if score != "-" %}{{ "%.1f"|format(score) }}{% else %}-{% endif %}|{% endfor %}
{% endfor %}
| **总分** |{% for scheme in schemes %}{{ "%.2f"|format(scheme.total_score) }}|{% endfor %}

---

## 📋 技术环节清单

{% for step in steps %}
### {{ step.index }}. {{ step.name }}

**需求描述：** {{ step.description }}

{% for sid, design in step.designs.items() %}
**{{ sid }} 视角：** {{ design }}

{% endfor %}

{% endfor %}

---

## ⚠️ 风险标注与修改历史

{% if risks %}
{% for risk in risks %}
- **{{ risk.scheme_id }}**：{{ risk.description }}
{% endfor %}
{% else %}
无特殊风险标注。
{% endif %}

{% if modifications %}
### 修改记录
{% for mod in modifications %}
- [{{ mod.agent_role }}] {{ mod.target_step }}: {{ mod.reason }}
{% endfor %}
{% endif %}

---

## 📋 可执行任务清单

> 可直接传递给 Claude Code Plan 模式、Superpowers writing-plans 等 AI 执行工具。
> MVP 范围: 前 {{ mvp_count }} 个任务为最小可行产品。

{% for task_item in task_list %}
### Task {{ task_item.id }}: {{ task_item.name }}{% if task_item.is_mvp %} 🔴 MVP{% endif %}
- [ ] {{ task_item.description }}
- **预计耗时**: {{ task_item.estimated_time }} | **优先级**: {{ task_item.priority }}{% if task_item.is_mvp %} | **MVP** ✅{% endif %}
{% if task_item.code_anchors %}
- **代码入口**: {% for anchor in task_item.code_anchors %}`{{ anchor }}` {% endfor %}
{% endif %}
{% if task_item.dependencies %}
- **依赖**: {% for dep in task_item.dependencies %}Task {{ dep }} {% endfor %}
{% endif %}

{% endfor %}

---

## 🚀 Goal 配置（可直接交给 GoalRunner 执行）

> 复制下方 JSON，直接调用 `GoalRunner.create_goal(**goal_config)` 即可执行。

```json
{{ goal_config | tojson }}
```

---

## 🔄 备选方案详情

{% for scheme in schemes %}
<details>
<summary>方案 {{ scheme.id }}：{{ scheme.object_name }}（{{ scheme.agent_role }}）— 得分 {{ "%.2f"|format(scheme.total_score) }}</summary>

{{ scheme.integrated_content }}

</details>

{% endfor %}
