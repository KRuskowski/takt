/// @file ui_adapter.h
/// @brief takt UI adapter for einheit-ui. Proxies the takt
/// REST API and renders pipeline state, workspaces, agents,
/// targets, and runs via HTMX + WebSocket.
// Copyright (c) 2026 Einheit Networks

#ifndef INCLUDE_EINHEIT_ADAPTERS_TAKT_UI_ADAPTER_H_
#define INCLUDE_EINHEIT_ADAPTERS_TAKT_UI_ADAPTER_H_

#include <memory>

#include "einheit/adapters/takt/takt_client.h"
#include "einheit/ui/adapter.h"

namespace einheit::adapters::takt {

/// Construct the takt UI adapter pointed at a running
/// takt REST API server.
/// @param cfg HTTP client configuration.
/// @returns Owning pointer to a ProductUiAdapter.
auto NewTaktUiAdapter(TaktClientConfig cfg)
    -> std::unique_ptr<ui::ProductUiAdapter>;

}  // namespace einheit::adapters::takt

#endif  // INCLUDE_EINHEIT_ADAPTERS_TAKT_UI_ADAPTER_H_
