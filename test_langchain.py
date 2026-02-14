"""Quick test: does LangChain + Open WebUI tool calling work?"""
import os
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool

load_dotenv()

# Setup LLM
llm = ChatOpenAI(
    base_url=f"{os.getenv('LOCAL_LLM_BASE_URL')}/api",
    api_key=os.getenv("LOCAL_LLM_API_KEY"),
    model=os.getenv("LOCAL_LLM_MODEL", "glm-4.7-flash:latest"),
    temperature=0.7,
    max_tokens=500,
)

# Simple test tool
@tool
def get_weather(city: str) -> str:
    """Get the current weather for a city."""
    return f"Weather in {city}: Sunny, 22°C"

# Test 1: Raw LLM call
print("=" * 50)
print("TEST 1: Raw LLM call (no tools)")
print("=" * 50)
try:
    response = llm.invoke("Say hello in 5 words")
    print(f"✅ Response: {response.content}")
except Exception as e:
    print(f"❌ Error: {e}")

# Test 2: LLM with tools bound
print("\n" + "=" * 50)
print("TEST 2: LLM with tool binding")
print("=" * 50)
try:
    llm_with_tools = llm.bind_tools([get_weather])
    response = llm_with_tools.invoke("What is the weather in Tokyo?")
    print(f"✅ Content: {response.content}")
    print(f"✅ Tool calls: {response.tool_calls}")
except Exception as e:
    print(f"❌ Error: {e}")

# Test 3: Full agent
print("\n" + "=" * 50)
print("TEST 3: Full Agent with AgentExecutor")
print("=" * 50)
try:
    from langchain.agents import create_tool_calling_agent, AgentExecutor
    from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

    prompt = ChatPromptTemplate.from_messages([
        ("system", "You are a helpful assistant."),
        ("human", "{input}"),
        MessagesPlaceholder(variable_name="agent_scratchpad"),
    ])

    agent = create_tool_calling_agent(llm, [get_weather], prompt)
    executor = AgentExecutor(agent=agent, tools=[get_weather], verbose=True, max_iterations=3)
    result = executor.invoke({"input": "What is the weather in Tokyo?"})
    print(f"\n✅ Final output: {result['output']}")
except Exception as e:
    print(f"❌ Error: {e}")