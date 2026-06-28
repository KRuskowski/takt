/// @file takt_client.h
/// @brief HTTP client for takt-service's REST API. Wraps
/// cpp-httplib so the adapter sees only parsed JSON or
/// typed errors.
// Copyright (c) 2026 Einheit Networks

#ifndef INCLUDE_EINHEIT_ADAPTERS_TAKT_TAKT_CLIENT_H_
#define INCLUDE_EINHEIT_ADAPTERS_TAKT_TAKT_CLIENT_H_

#include <chrono>
#include <expected>
#include <memory>
#include <string>
#include <string_view>

#include <nlohmann/json.hpp>

#include "einheit/ui/error.h"

namespace einheit::adapters::takt {

/// Errors surfaced by the takt HTTP client.
enum class TaktClientError {
  /// TCP connect, read, write, or timeout.
  Network,
  /// Service returned a non-2xx HTTP status.
  HttpError,
  /// Response body was not valid JSON.
  Json,
  /// URL configured at construction time was bad.
  BadUrl,
};

/// Settings for a TaktClient instance.
struct TaktClientConfig {
  /// Base URL of the takt REST API.
  std::string base_url = "http://127.0.0.1:7433";
  /// Per-request timeout.
  std::chrono::milliseconds timeout{5000};
};

/// HTTP client for the takt REST API.
class TaktClient {
 public:
  /// @param cfg Client configuration.
  explicit TaktClient(TaktClientConfig cfg);
  ~TaktClient();

  TaktClient(const TaktClient &) = delete;
  auto operator=(const TaktClient &)
      -> TaktClient & = delete;
  TaktClient(TaktClient &&) noexcept;
  auto operator=(TaktClient &&) noexcept -> TaktClient &;

  /// GET a path and parse the response as JSON.
  /// @param path Path component, must start with '/'.
  /// @returns Parsed JSON or TaktClientError.
  auto Get(std::string_view path) const
      -> std::expected<nlohmann::json,
                       ui::Error<TaktClientError>>;

  /// POST a path with an optional JSON body.
  /// @param path Path component.
  /// @param body Request body.
  /// @param content_type Content-Type header.
  /// @returns Parsed JSON or TaktClientError.
  auto Post(std::string_view path,
            std::string_view body = "",
            std::string_view content_type =
                "application/json") const
      -> std::expected<nlohmann::json,
                       ui::Error<TaktClientError>>;

  /// PUT a path with a JSON body.
  /// @param path Path component.
  /// @param body Request body.
  /// @returns Parsed JSON or TaktClientError.
  auto Put(std::string_view path,
           std::string_view body) const
      -> std::expected<nlohmann::json,
                       ui::Error<TaktClientError>>;

  /// DELETE a path.
  /// @param path Path component.
  /// @returns Parsed JSON or TaktClientError.
  auto Delete(std::string_view path) const
      -> std::expected<nlohmann::json,
                       ui::Error<TaktClientError>>;

 private:
  struct Impl;
  std::unique_ptr<Impl> impl_;
};

}  // namespace einheit::adapters::takt

#endif  // INCLUDE_EINHEIT_ADAPTERS_TAKT_TAKT_CLIENT_H_
