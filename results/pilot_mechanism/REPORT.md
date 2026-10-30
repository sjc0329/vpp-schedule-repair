# 总计划保持与协调择优：正确性核验

仅解析正确性检查，不是正式效果或效率实验。未生成新的正式数据集。

- same_time_compensation_and_coordination: PASS
- no_cancellation_across_time: PASS
- nonzero_executed_prefix_and_remaining_task: PASS
- ev_arrival_energy_injection: PASS
- budget_tradeoff: PASS
- restricted_scope_failure: PASS
- simulated_interruption_fallback: PASS
- simulated_total_interruption_fallback: PASS

中断检查使用模拟返回状态，不代表真实求解超时的测量结果。
完整方案、阶段状态、求解界和独立核验结果见 checks.json。
