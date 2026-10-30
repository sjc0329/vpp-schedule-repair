# 十二结构模板输入核验

本轮只做输入构建与结构核验，不是正式主实验、文献对比或消融。
处理12/12，结构确认12，需复核0。
求解调用27次：原计划、事件后经济参照及G1零预算单目标探针。

|模板|状态|结构诊断|
|---|---|---|
|TPL-G1-1|QUALIFIED|{"raw_economic_deviation": 66.37600418425733, "zero_budget_deviation": 8.000000000000021, "observed_improvement": 58.376004184257305, "probe_optimal": true, "interpretation": "An observed selectable economic-band plan, not an independent generalization result"}|
|TPL-G1-2|QUALIFIED|{"raw_economic_deviation": 60.975046855806085, "zero_budget_deviation": 8.00000000000118, "observed_improvement": 52.975046855804905, "probe_optimal": true, "interpretation": "An observed selectable economic-band plan, not an independent generalization result"}|
|TPL-G1-3|QUALIFIED|{"raw_economic_deviation": 31.366218152327534, "zero_budget_deviation": 8.0, "observed_improvement": 23.366218152327534, "probe_optimal": true, "interpretation": "An observed selectable economic-band plan, not an independent generalization result"}|
|TPL-G2-1|QUALIFIED|{"direct_absorption_energy": 8.95717846477207, "local_absorption_energy": 8.95717846477207, "minimum_direct_participants": 0, "sufficient": true, "witness_extra_cost": 0.0}|
|TPL-G2-2|QUALIFIED|{"direct_absorption_energy": 10.0, "local_absorption_energy": 0.0, "minimum_direct_participants": 1, "sufficient": true, "witness_extra_cost": 0.0}|
|TPL-G2-3|QUALIFIED|{"direct_absorption_energy": 5.99357676971581, "local_absorption_energy": 0.0, "minimum_direct_participants": null, "sufficient": false, "witness_extra_cost": null}|
|TPL-G3-1|QUALIFIED|{"ev_actual_energy": 45.0, "departure_requirement": 40.0, "service_safe_discharge_without_recharge": 4.75, "event_energy": 8.0, "post_event_charge_input_capacity": 0.0, "minimum_charge_input_for_standalone_event": 3.601108033240995, "standalone_event_energy_can_be_restored": false, "interpretation": "Single-resource energy/window diagnosis; not a complete plan-repair witness"}|
|TPL-G3-2|QUALIFIED|{"ev_actual_energy": 45.0, "departure_requirement": 40.0, "service_safe_discharge_without_recharge": 4.75, "event_energy": 8.0, "post_event_charge_input_capacity": 20.0, "minimum_charge_input_for_standalone_event": 3.601108033240995, "standalone_event_energy_can_be_restored": true, "interpretation": "Single-resource energy/window diagnosis; not a complete plan-repair witness"}|
|TPL-G3-3|QUALIFIED|{"ev_actual_energy": 45.0, "departure_requirement": 40.0, "service_safe_discharge_without_recharge": 4.75, "event_energy": 8.0, "post_event_charge_input_capacity": 40.0, "minimum_charge_input_for_standalone_event": 3.601108033240995, "standalone_event_energy_can_be_restored": true, "interpretation": "Single-resource energy/window diagnosis; not a complete plan-repair witness"}|
|TPL-G4-1|QUALIFIED|{"actual_witness_participants": 1, "participation_lower_bound": 1, "expected_participants": 1, "witness_total_deviation": 0.0, "witness_extra_cost": 1.6800000000000068, "available_test_budget": 1.92, "source_cost_interpretation": "Contracted standby response compensation plus throughput cost"}|
|TPL-G4-2|QUALIFIED|{"actual_witness_participants": 2, "participation_lower_bound": 2, "expected_participants": 2, "witness_total_deviation": 0.0, "witness_extra_cost": 1.6800000000000068, "available_test_budget": 1.92, "source_cost_interpretation": "Contracted standby response compensation plus throughput cost"}|
|TPL-G4-3|QUALIFIED|{"actual_witness_participants": 3, "participation_lower_bound": 3, "expected_participants": 3, "witness_total_deviation": 0.0, "witness_extra_cost": 1.6800000000000068, "available_test_budget": 1.92, "source_cost_interpretation": "Contracted standby response compensation plus throughput cost"}|

## 解释

G1探针检验经济带内的计划择优空间；不通过事后改变经济基线制造差距。
G2构造方案固定旧设备和任务；不满足证书不推出完整模型不可修复。
G3诊断EV服务电量和充电窗口，不能当成完整修复方案的可行性保证。
G4为高响应补偿备用资源，费用1.01元/kWh高于日内最高购电价1.0元/kWh；
因此经济原计划倾向不启用。事件后以补偿代价调用备用，可保持对外计划。
该费用代表合同补偿与吞吐成本，不能当成通用储能退化成本估计。
G4总容量100kWh、总功率20kW、初始60%和终端40%固定，仅分布不同。
所有参数在求解前记录并冻结；未确认结构的模板保留，不自动调整或删除。
四类模板具有针对性，后续仍需相邻条件与不同曲线验证。
