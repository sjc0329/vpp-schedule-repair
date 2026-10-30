# 路线 A：基础正确性核验

本报告不是性能实验，不证明速度或效果优势。

- 核验实例：4
- 通过：4
- 未通过：0
- 尚未确定：0
- 预期物理不可行：1
- HiGHS：1.15.1

|实例|结果|分类|物理最小修改/kWh|经济最优带内修改/kWh|
|---|---|---|---:|---:|
|N1-S1|PASS|VALIDATED_FEASIBLE|0.0|0.0|
|N2-S1|PASS|VALIDATED_FEASIBLE|4.000000000000001|6.469135802469134|
|R1-S2|PASS|VALIDATED_FEASIBLE|0.0|0.0|
|R4-S3|PASS|PROVEN_PHYSICALLY_INFEASIBLE|—|—|

## 语义与独立核验器检查

- event_id_collision: PASS
- stale_event_version: PASS
- checker_rejects_executed_prefix: PASS
- checker_rejects_bus_violation: PASS
- checker_rejects_stale_plan: PASS
- checker_rejects_after_departure: PASS
- checker_rejects_scope_violation: PASS
- checker_rejects_cost_violation: PASS
- idle_modes_not_counted_as_adjustments: PASS
- checker_rejects_state_corruption: PASS

## 边界

增量更新复用同一个 HiGHS 对象；不声称复用分支搜索树。
本次仅核验确定性模型与解析实例；没有实施后续能力择优、连续事件效果对比或正式性能实验。
所有经济上限均记录实际值；数值容差独立于经济预算。
逐例 JSON 包含原计划、更新后的状态、实际增量矩阵审核、完整解与独立物理残差。
