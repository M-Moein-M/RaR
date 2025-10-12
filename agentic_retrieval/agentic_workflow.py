# Adapted from LangChain (MIT License) – see LICENSE.langchain

from typing import List, Annotated, TypedDict, Literal
import operator
from pydantic import BaseModel, Field

from utils import init_chat_model, get_config_value, searxng_search
from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig
from langgraph.graph import MessagesState, START, END, StateGraph
from langgraph.types import Command, Send

from configuration import Configuration
from prompts_radiology import SUPERVISOR_INSTRUCTIONS, RESEARCH_INSTRUCTIONS

import logging
from pathlib import Path
import json

logger = logging.getLogger(__name__)


## Tools factory - will be initialized based on configuration
def get_search_tool(config: RunnableConfig):
    """Get the appropriate search tool based on configuration. Only searxng is supported."""
    configurable = Configuration.from_runnable_config(config)
    search_api = get_config_value(configurable.search_api)

    if search_api.lower() == "searxng":
        return searxng_search
    else:
        raise NotImplementedError(
            f"The search API '{search_api}' is not supported in this implementation. "
            f"Only 'searxng' is supported. Please set search_api to 'searxng'."
        )


@tool
class Section(BaseModel):
    name: str = Field(
        description="Name for this section of the report.",
    )
    description: str = Field(
        description="Research scope for this section of the report.",
    )
    content: str = Field(
        description="The content of the section, including the list of URLs used for research. "
    )


@tool
class Sections(BaseModel):
    sections: List[str] = Field(
        description="Sections of the report. Each section should be a string that describes the research plan for that section. ",
    )


@tool
class Introduction(BaseModel):
    name: str = Field(
        description="Name for the report.",
    )
    content: str = Field(
        description="The content of the report."
    )


@tool
class Conclusion(BaseModel):
    name: str = Field(
        description="Name for the conclusion of the report.",
    )
    content: str = Field(
        description="The content of the conclusion, summarizing the report and not giving away the answer."
    )


## State
class ReportStateOutput(TypedDict):
    final_report: str  # Final report


class ReportState(MessagesState):
    sections: list[str]  # List of report sections 
    completed_sections: Annotated[list, operator.add]  # Send() API key
    final_report: str  # Final report


class SectionState(MessagesState):
    section: str  # Report section  
    completed_sections: list[Section]  # Final key we duplicate in outer state for Send() API


class SectionOutputState(TypedDict):
    completed_sections: list[Section]  # Final key we duplicate in outer state for Send() API


# Tool lists will be built dynamically based on configuration
def get_supervisor_tools(config: RunnableConfig):
    """Get supervisor tools based on configuration"""
    search_tool = get_search_tool(config)
    tool_list = [search_tool, Sections, Introduction, Conclusion]
    return tool_list, {tool.name: tool for tool in tool_list}


def get_research_tools(config: RunnableConfig):
    """Get research tools based on configuration"""
    search_tool = get_search_tool(config)
    tool_list = [search_tool, Section]
    return tool_list, {tool.name: tool for tool in tool_list}


async def supervisor(state: ReportState, config: RunnableConfig):
    """LLM decides whether to call a tool or not"""
    messages = state["messages"]

    configurable = Configuration.from_runnable_config(config)
    supervisor_model = get_config_value(configurable.supervisor_model)
    llm = init_chat_model(model=supervisor_model)

    # If all planned sections are completed and final report not yet written, prompt for intro/conclusion
    if (
        state.get("sections")
        #and len(state.get("completed_sections", [])) == len(state["sections"])
        and not state.get("final_report")
    ):
        research_complete_message = {
            "role": "user",
            "content": (
                "Research is complete. Call the 'Introduction' Tool to write Introduction and Conclusion. "
                "Do not indicate which option is actually correct. Here are the completed main body sections:\n\n"
                + "\n\n".join([s.content for s in state["completed_sections"]])
            ),
        }
        messages = messages + [research_complete_message]

    supervisor_tool_list, _ = get_supervisor_tools(config)

    return {
        "messages": [
            await llm.bind_tools(supervisor_tool_list, parallel_tool_calls=False).ainvoke(
                [{"role": "system", "content": SUPERVISOR_INSTRUCTIONS}] + messages
            )
        ]
    }


async def supervisor_tools(state: ReportState, config: RunnableConfig) -> Command[Literal["supervisor", "research_team", "__end__"]]:
    """Performs the tool call and sends to the research agent"""
    result = []
    sections_list: List[str] = []
    intro_content = None
    conclusion_content = None

    _, supervisor_tools_by_name = get_supervisor_tools(config)

    last_message = state["messages"][-1]

    # Process each tool call
    for tool_call in getattr(last_message, "tool_calls", []):
        if tool_call["args"] is None:
            error_content = (
                f"Error: Tool call to '{tool_call['name']}' failed because arguments were missing. "
                "Please review the instructions and try again with valid arguments."
            )
            result.append({"role": "tool", "content": error_content, "tool_call_id": tool_call["id"]})
            logger.warning("Tool call for '%s' had no args.", tool_call["name"])
            continue

        tool_obj = supervisor_tools_by_name[tool_call["name"]]
        if hasattr(tool_obj, "ainvoke"):
            observation = await tool_obj.ainvoke(tool_call["args"])
        else:
            observation = tool_obj.invoke(tool_call["args"])

        result.append({
            "role": "tool",
            "content": observation,
            "name": tool_call["name"],
            "tool_call_id": tool_call["id"],
        })

        if tool_call["name"] == "Sections":
            # If the tool returned one blob, split it into individual sections; otherwise keep as-is.
            raw_sections = observation.sections
            if len(raw_sections) == 1:
                sections_list = [
                    sec.strip()
                    for sec in raw_sections[0].split("\n\n")
                    if sec.strip()
                ]
            else:
                sections_list = raw_sections

        elif tool_call["name"] == "Introduction":
            if not observation.content.startswith("# "):
                intro_content = f"# {observation.name}\n\n{observation.content}"
            else:
                intro_content = observation.content

        elif tool_call["name"] == "Conclusion":
            if not observation.content.startswith("## "):
                conclusion_content = f"## {observation.name}\n\n{observation.content}"
            else:
                conclusion_content = observation.content

    if sections_list:
        return Command(
            goto=[Send("research_team", {"section": s}) for s in sections_list],
            update={
                "messages": result,
                "sections": sections_list,
            },
        )
    elif intro_content:
        body_sections = "\n\n".join([s.content for s in state["completed_sections"]])
        result.append({
            "role": "user",
            "content": (
                "Introduction written. Now write a conclusion section. Write a neutral synthesis. "
                "Do not indicate which option is actually correct."
            ),
        })
        return Command(goto="supervisor", update={"final_report": intro_content, "messages": result})
    elif conclusion_content:
        intro = state.get("final_report", "")
        body_sections = "\n\n".join([s.content for s in state["completed_sections"]])
        complete_report = f"{intro}\n\n{body_sections}\n\n{conclusion_content}"
        result.append({
            "role": "user",
            "content": "Report is now complete with introduction, body sections, and conclusion.",
        })
        return Command(goto="supervisor", update={"final_report": complete_report, "messages": result})
    else:
        return Command(goto="supervisor", update={"messages": result})


async def supervisor_should_continue(state: ReportState) -> Literal["supervisor_tools", END]:
    """Decide whether to continue to tools or end based on tool calls (valid or invalid)."""
    last_message = state["messages"][-1]
    if getattr(last_message, "tool_calls", None) or getattr(last_message, "invalid_tool_calls", None):
        return "supervisor_tools"
    logger.info("Supervisor has assembled the final report, ending workflow.")
    return END


async def research_agent(state: SectionState, config: RunnableConfig):
    """LLM decides whether to call a tool or not"""
    configurable = Configuration.from_runnable_config(config)
    researcher_model = get_config_value(configurable.researcher_model)
    llm = init_chat_model(model=researcher_model)

    research_tool_list, _ = get_research_tools(config)
    return {
        "messages": [
            await llm.bind_tools(research_tool_list).ainvoke(
                [{"role": "system", "content": RESEARCH_INSTRUCTIONS.format(section_description=state["section"])}]
                + state["messages"]
            )
        ]
    }


async def research_agent_tools(state: SectionState, config: RunnableConfig):
    """Performs the tool call and route to supervisor or continue the research loop"""
    result = []
    completed_section = None

    _, research_tools_by_name = get_research_tools(config)

    for tool_call in getattr(state["messages"][-1], "tool_calls", []):
        tool_obj = research_tools_by_name[tool_call["name"]]
        if hasattr(tool_obj, "ainvoke"):
            observation = await tool_obj.ainvoke(tool_call["args"])
        else:
            observation = tool_obj.invoke(tool_call["args"])

        result.append({
            "role": "tool",
            "content": observation,
            "name": tool_call["name"],
            "tool_call_id": tool_call["id"],
        })

        if tool_call["name"] == "Section":
            completed_section = observation

    if completed_section:
        return {"messages": result, "completed_sections": [completed_section]}
    else:
        return {"messages": result}


async def research_agent_should_continue(state: SectionState) -> Literal["research_agent_tools", END]:
    """Decide if we should continue based on whether the LLM made a tool call."""
    last_message = state["messages"][-1]
    if getattr(last_message, "tool_calls", None):
        return "research_agent_tools"
    return END


# ---------------------------------------------------------------------
# Build the multi-agent workflow
# ---------------------------------------------------------------------

# Research agent workflow
research_builder = StateGraph(SectionState, output=SectionOutputState, config_schema=Configuration)
research_builder.add_node("research_agent", research_agent)
research_builder.add_node("research_agent_tools", research_agent_tools)
research_builder.add_edge(START, "research_agent")
research_builder.add_conditional_edges(
    "research_agent",
    research_agent_should_continue,
    {
        "research_agent_tools": "research_agent_tools",
        END: END,
    },
)
research_builder.add_edge("research_agent_tools", "research_agent")

# Supervisor workflow
supervisor_builder = StateGraph(ReportState, input=MessagesState, output=ReportStateOutput, config_schema=Configuration)
supervisor_builder.add_node("supervisor", supervisor)
supervisor_builder.add_node("supervisor_tools", supervisor_tools)
supervisor_builder.add_node("research_team", research_builder.compile())

supervisor_builder.add_edge(START, "supervisor")
supervisor_builder.add_conditional_edges(
    "supervisor",
    supervisor_should_continue,
    {
        "supervisor_tools": "supervisor_tools",
        END: END,
    },
)
supervisor_builder.add_edge("research_team", "supervisor")

graph = supervisor_builder.compile()
