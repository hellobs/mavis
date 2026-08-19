"""自研生成式智能体仿真框架

分层:
- framework/core    : Agent 生命周期、记忆、日程、空间(纯逻辑,零渲染依赖)
- framework/runtime : 运行调度、LLM 适配、消息协议(protocol)
- framework/scene   : 空间/寻路、场景加载
- framework/config  : 配置 schema

业务层(scenarios/)与前端层(frontend/)与框架层分离:
- 换业务 = 改 scenarios/ 配置
- 换前端(Phaser/Unity)= 按 framework/runtime/protocol.py 消费消息
"""
