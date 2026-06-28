/// @file adapter.h
/// @brief takt CLI adapter factory.
// Copyright (c) 2026 Einheit Networks

#ifndef ADAPTERS_TAKT_ADAPTER_H_
#define ADAPTERS_TAKT_ADAPTER_H_

#include <memory>
#include <string>

#include "einheit/cli/adapter.h"

namespace einheit::adapters::takt {

struct TaktCliConfig {
  std::string api_url = "http://127.0.0.1:7433";
};

auto NewTaktAdapter(TaktCliConfig cfg = {})
    -> std::unique_ptr<cli::ProductAdapter>;

}  // namespace einheit::adapters::takt

#endif  // ADAPTERS_TAKT_ADAPTER_H_
