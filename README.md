# 💡 OpenHueUI — OpenWebUI Workspace Tool

A production-ready Open WebUI integration that enables LLMs to control Philips Hue lighting systems through a native Tools class architecture. This tool allows for precise, natural language control of individual lights, groups, and scenes.

## 📋 Overview
OpenHueUI is structured as a native Tools class within OpenWebUI. It eliminates the need for complex middleware by communicating directly with the Philips Hue Bridge API. It is designed with a "Group-First" philosophy to ensure synchronized lighting transitions and reduced API overhead.

## 🧩 Key Features

| Feature             | Description                                                                                   |
|---------------------|---------------------------------------------------------------------------------------|
| Production Ready    | Robust error handling with standardized JSON response envelopes                             |
| Configurable Valves | Admin-controlled Bridge IP and API tokens via the OpenWebUI GUI                                |
| Group-Centric Logic | Prioritizes group commands over individual light calls for synchronization                     |
| Color Intelligence  | Built-in RGB to CIE 1931 XY chromaticity conversion                                           |
| Mood Presets        | Pre-defined lighting scenes for quick atmosphere changes                                      |
| Native Tooling      | Fully integrated as a tool for LLMs to manipulate physical environment                           |

## 🛠Configuration & Setup

### Configuration Valves
The following parameters can be configured via the OpenWebUI Valve settings:

- **hue_bridge_ip**: The IP address of your Philips Hue Bridge.
- **hue_api_key**: Your authorized Hue API token.

### 🧩 Functional Modules

#### 💡 Lighting Control

- **Basic States**: Toggle lights on/off across the entire home or specific rooms.
- **Brightness & Dimming**: Precise control over luminosity levels.
- **Color Mapping**: Transition lights to specific hex colors or presets.

#### 🏠 Room & Group Management

- **Group Control**: Target specific zones (e.g., "Living Room") to ensure consistent lighting.
- **State Querying**: Check the current status of lights and active scenes.

#### 🎨 Preset Scenes

- **Quick Moods**: Trigger built-in presets (e.g., "Reading", "Concentrate", "Nightlight").
- **Custom Scenes**: Activate user-defined Hue scenes via the bridge.

### 🏗 Architecture & Logic

#### Design Principles

- **Group-First Approach**: The tool attempts to resolve requests to groups first to avoid "popcorn" effects (lights turning on one by one).
- **Safety Validation**: Validates brightness and color ranges before sending commands to the bridge to prevent API errors.
- **Atomic Responses**: Returns clear, concise confirmation of the action taken, allowing the LLM to verify the state change.

## 🚀 Installation

1. OpenWebUI: Navigate to the Workspace → Tools section.
2. Import: Create a new tool and paste the hue_tool.py code.
3. Configure: Enter your Bridge IP and API Key in the Valve settings.
4. Assign: Attach the tool to your preferred Model (e.g., GPT-4 or Claude 3).

## 📋 API Response Format

All tools return a structured response to the LLM:

```json
{
  "status": "success" | "error",
  "message": "Detailed description of the action performed",
  "data": { "light_id": 1, "state": "on", "brightness": 80 }
}
```

## 🤝Contributing

Feel free to open an issue or submit a pull request to add new lighting patterns or integration features.

Developed for OpenWebUI to bridge the gap between LLM reasoning and home automation.
