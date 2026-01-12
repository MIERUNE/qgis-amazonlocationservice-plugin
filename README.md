# Amazon Location Service Plugin

Read this in other languages: [Japanese](./README_ja.md)

![logo](img/logo.png)

This plugin uses the functionality of Amazon Location Service v2 in QGIS.  

- [QGIS](https://qgis.org)  
- [Amazon Location Service](https://aws.amazon.com/location)  

## QGIS Python Plugins Repository

[Amazon Location Service Plugin](https://plugins.qgis.org/plugins/location_service)  

## blog


## Usage

### Building an Amazon Location Service API Key

![location-service](img/location-service.png)

[Building an Amazon Location Service v2 API Key](https://memo.dayjournal.dev/memo/amazon-location-service-007)  

### Install QGIS Plugin

![plugin](img/plugin.png)

1. Select "Plugins" → "Manage and Install Plugins..."
2. Search for "Amazon Location Service"

Plugins can also be installed by loading a [zip file](https://github.com/MIERUNE/qgis-amazonlocationservice-plugin/releases).

### Menu

![menu](img/menu.png)

- Config: Set each region name and API key
- Maps: Map display function
- Places: Geocoding function
- Routes: Routing function
- Terms: Display Terms of Use page

### Config Function

![config](img/config.png)

1. Click the “Config” menu
2. Set each region name and API key
    - Region: ap-xxxxx
    - API Key: v1.public.xxxxx
3. Click “Save“

### Maps Function

![maps](img/maps.gif)

1. Click the “Maps” menu
2. Select “Map Name“
3. Click “Add“
4. The map is displayed as a layer

### Places Function

![places](img/places.gif)

1. Click the “Places” menu
2. Select “Select Function“
3. Enter text in “QueryText“
4. Click “Get Location“
5. Click on the location you wish to search
6. Click “Search”
7. Search results are displayed in layers

※ As of January 2025, only ”SearchText” is available.

### Routes Function

![routes](img/routes.gif)

1. Click the “Routes” menu
2. Select “Select Function“
3. Click “Get Location(Starting Point)“
4. Click the starting point
5. Click “Get Location(End Point)“
6. Click on the endpoint
7. Click “Search”
8. Search results are displayed in layers

※ As of January 2025, only ”CalculateRoute” is available.

### Terms Function

1. Click the “Terms” menu
2. The Terms of Use page will be displayed in your browser.

### Terms

[AWS Service Terms](https://aws.amazon.com/jp/service-terms)

Amazon Location Service has terms of use for data usage. Please check the section “82. Amazon Location Service” and use the service at your own risk. The developer is not responsible for any damages that may occur in connection with the use of this service.  

When using HERE as a provider, in addition to the basic terms and conditions, you may not.  

a. Store or cache any Location Data for Japan, including any geocoding or reverse-geocoding results.  
b. Layer routes from HERE on top of a map from another third-party provider, or layer routes from another third-party provider on top of maps from HERE.  

## Development

### Requirements

- [uv](https://docs.astral.sh/uv/)
- QGIS 3.x

### Setup

```bash
# Install dependencies
uv sync

# Lint
uv run ruff check .

# Format
uv run ruff format .
```

### Local Development

Create a symbolic link to the QGIS plugins directory:

**macOS:**
```bash
ln -s /path/to/location_service ~/Library/Application\ Support/QGIS/QGIS3/profiles/default/python/plugins/location_service
```

**Windows:**
```powershell
mklink /D "%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\location_service" "C:\path\to\location_service"
```

**Linux:**
```bash
ln -s /path/to/location_service ~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/location_service
```

After editing the code, reload the plugin in QGIS to see the changes.

## License

Python modules are released under the GNU General Public License v2.0

Copyright (c) 2024-2026 MIERUNE Inc.
