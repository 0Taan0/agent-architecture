# Erste version hier wird klar gestellt welhe knoten im prozess mas vorhanden sind und wie sie miteinander verbunden sind.
# Vielleicht mit jev oder laya veruschen  muss es erst versuchen und dann schauen ob es klappt.

def discover(ctx):
    agents = {}
    for cls in ctx.classes_inheriting("core.base.BaseAgent"):
        agent = ctx.agent(
            id=ctx.stable_id(cls),
            name=cls.name,
            input_schema=ctx.first_param_annotation(cls, "run"),
            output_schema=ctx.return_annotation(cls, "run"),
            tools=ctx.literal_list(cls, "TOOLS"),
            code_ref=ctx.ref(cls),
        )
        agents[cls.name] = agent
        yield agent
    # control flow of this MAS: the PIPELINE list in pipeline.py
    order = ["ProcessAnalyzerAgent", "ImprovementProposerAgent", "CriticAgent"]
    for a, b in zip(order, order[1:]):
        yield ctx.connection(agents[a].id, agents[b].id)
    for call in ctx.calls_to("registry.register"):
        yield ctx.unresolved("dynamic_tool_registration", f"{call.file}:{call.line}",
                             "tools registered at runtime are not visible statically")
