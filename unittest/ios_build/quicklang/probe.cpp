// Copyright (c) 2026 OceanBase.
// SPDX-License-Identifier: Apache-2.0
#include "probe.h"
#include "schema_snapshot.h"
#include "seekdb_ios.h"
#include "observer/ob_server.h"
#include "common/mysqlclient/ob_mysql_proxy.h"
#include "common/mysqlclient/ob_mysql_result.h"
#include "common/mysqlclient/ob_mysql_transaction.h"
#include "lib/thread/protected_stack_allocator.h"
#include "lib/worker.h"
#include "share/rc/ob_server_runtime.h"
#include <cstdio>
#include <cstdlib>
#include <string>
#include <vector>

using namespace oceanbase;
using namespace oceanbase::common;
namespace {
using Rows = std::vector<std::vector<std::string>>;

/** Execute isolated SQL fixtures, stopping at the first failed assertion. */
class Suite {
public:
  /** Borrow the engine proxy and evidence file for one suite execution. */
  Suite(ObMySQLProxy &proxy, FILE *report) : proxy_(proxy), report_(report) {}
  /** Roll back any unfinished transaction, including after an assertion failure. */
  ~Suite() { if (transaction_.is_started()) { transaction_.end(false); } }
  /** Return the first database or assertion error. */
  int result() const { return result_; }
  /** Execute a mutation and optionally check affected rows or an exact error code. */
  void write(const char *name, const char *sql, int64_t affected = -1, int expected_error = 0)
  {
    if (result_ != 0) { return; }
    int64_t actual = 0;
    int ret = client().write(sql, actual);
    if (ret == expected_error) {
      ret = expected_error != 0 || affected < 0 || affected == actual ? 0 : OB_ERR_UNEXPECTED;
    } else if (ret == 0) {
      ret = OB_ERR_UNEXPECTED;
    }
    record(name, ret);
  }
  /** Check every row and column; i: cells are integers, s: cells are exact UTF-8 strings. */
  void read(const char *name, const char *sql, const Rows &expected)
  {
    if (result_ != 0) { return; }
    ObISQLClient::ReadResult result;
    int ret = client().read(result, sql);
    auto *rows = ret == 0 ? result.get_result() : nullptr;
    if (ret == 0 && rows == nullptr) { ret = OB_ERR_UNEXPECTED; }
    for (const auto &row : expected) {
      if (ret != 0) { break; }
      if (rows->get_column_count() != static_cast<int64_t>(row.size())) {
        ret = OB_ERR_UNEXPECTED;
        break;
      }
      ret = rows->next();
      for (size_t col = 0; ret == 0 && col < row.size(); ++col) {
        const std::string &cell = row[col];
        if (cell.compare(0, 2, "i:") == 0) {
          int64_t value = 0;
          ret = rows->get_int(static_cast<int64_t>(col), value);
          if (ret == 0 && value != std::stoll(cell.substr(2))) { ret = OB_ERR_UNEXPECTED; }
        } else {
          ObString value;
          ret = rows->get_varchar(static_cast<int64_t>(col), value);
          if (ret == 0 && std::string(value.empty() ? "" : value.ptr(), value.length()) != cell.substr(2)) { ret = OB_ERR_UNEXPECTED; }
        }
      }
    }
    if (ret == 0 && rows->next() != OB_ITER_END) { ret = OB_ERR_UNEXPECTED; }
    record(name, ret);
  }
  /** Pin subsequent statements to a single transactional connection. */
  void begin(const char *name)
  {
    if (result_ == 0) { record(name, transaction_.start(&proxy_)); }
  }
  /** Commit or roll back the pinned connection and record the outcome. */
  void end(const char *name, bool commit)
  {
    if (result_ == 0) { record(name, transaction_.end(commit)); }
  }
private:
  /** Select the pinned connection while a transaction is active. */
  ObISQLClient &client() { return transaction_.is_started() ? static_cast<ObISQLClient &>(transaction_) : proxy_; }
  /** Flush each result so a later engine crash cannot erase completed evidence. */
  void record(const char *name, int ret)
  {
    result_ = ret;
    if (std::fprintf(report_, "{\"step\":%d,\"case\":\"%s\",\"result\":%d}\n", ++step_, name, ret) < 0 || std::fflush(report_) != 0) {
      result_ = OB_IO_ERROR;
    }
  }
  ObMySQLProxy &proxy_;
  ObMySQLTransaction transaction_;
  FILE *report_;
  int result_ = 0;
  int step_ = 0;
};

/** Create the exact application schemas in a dedicated test database and clear only its fixtures. */
void schema_cases(Suite &s)
{
  s.write("schema.database", "CREATE DATABASE IF NOT EXISTS ql_ios_probe");
  for (const char *ddl : quicklang_schema) { s.write("schema.create", ddl); }
  for (const char *ddl : quicklang_schema) { s.write("schema.idempotent", ddl); }
  s.read("schema.inventory", "SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA='ql_ios_probe'", {{"i:10"}});
  for (const char *name : {"ql_ai_profiles", "ql_app_state", "ql_interpretation_chunk", "ql_interpretation_session", "ql_listening", "ql_review_event", "ql_review_state", "ql_user_settings", "ql_word", "wordbook"}) {
    s.write("fixture.reset", (std::string("DELETE FROM ql_ios_probe.") + name).c_str());
  }
}

/** Cover state batches, JSON settings, binary key collation and AI-profile initialization. */
void settings_cases(Suite &s)
{
  s.write("state.insert", "INSERT INTO ql_ios_probe.ql_app_state VALUES ('Key','first'),('key','second')", 2);
  s.write("state.upsert", "INSERT INTO ql_ios_probe.ql_app_state VALUES ('Key',CONVERT(X'275c00e4b8ad' USING utf8mb4)) ON DUPLICATE KEY UPDATE state_value=VALUES(state_value)");
  s.read("state.exact_bytes", "SELECT state_key,HEX(state_value) FROM ql_ios_probe.ql_app_state ORDER BY state_key", {{"s:Key","s:275C00E4B8AD"},{"s:key","s:7365636F6E64"}});
  s.begin("state.begin_rollback");
  s.write("state.batch_delete", "DELETE FROM ql_ios_probe.ql_app_state WHERE state_key='key'", 1);
  s.write("state.batch_insert", "INSERT INTO ql_ios_probe.ql_app_state VALUES ('rollback','discard')", 1);
  s.end("state.rollback", false);
  s.read("state.rollback_restored", "SELECT state_key FROM ql_ios_probe.ql_app_state ORDER BY state_key", {{"s:Key"},{"s:key"}});
  s.write("settings.users", "INSERT INTO ql_ios_probe.ql_user_settings VALUES ('alice','pref','{\"count\":27}'),('bob','pref','{\"count\":45}')", 2);
  s.write("settings.upsert", "INSERT INTO ql_ios_probe.ql_user_settings VALUES ('alice','pref','{\"count\":30}') ON DUPLICATE KEY UPDATE setting_value=VALUES(setting_value)");
  s.read("settings.isolation", "SELECT user_id,JSON_UNQUOTE(JSON_EXTRACT(setting_value,'$.count')) FROM ql_ios_probe.ql_user_settings ORDER BY user_id", {{"s:alice","s:30"},{"s:bob","s:45"}});
  s.write("settings.delete", "DELETE FROM ql_ios_probe.ql_user_settings WHERE user_id='alice' AND setting_key='pref'", 1);
  s.read("settings.other_user_preserved", "SELECT user_id FROM ql_ios_probe.ql_user_settings", {{"s:bob"}});
  s.write("profiles.initialize", "INSERT IGNORE INTO ql_ios_probe.ql_ai_profiles VALUES (1,'{\"profiles\":[],\"active_id\":null,\"master_key_initialized\":false}')", 1);
  s.write("profiles.update", "UPDATE ql_ios_probe.ql_ai_profiles SET payload='{\"profiles\":[],\"active_id\":\"probe\",\"master_key_initialized\":true}' WHERE singleton_id=1", 1);
  s.write("profiles.idempotent", "INSERT IGNORE INTO ql_ios_probe.ql_ai_profiles VALUES (1,'{}')", 0);
  s.read("profiles.preserved", "SELECT JSON_UNQUOTE(JSON_EXTRACT(payload,'$.active_id')) FROM ql_ios_probe.ql_ai_profiles WHERE singleton_id=1", {{"s:probe"}});
}

/** Check interpretation ordering, chunk upserts and atomic owner-scoped deletion. */
void interpretation_cases(Suite &s)
{
  s.write("session.insert", "INSERT INTO ql_ios_probe.ql_interpretation_session VALUES ('s1','alice',10,'old'),('s2','alice',20,'new'),('s3','bob',30,'other')", 3);
  s.write("session.upsert", "INSERT INTO ql_ios_probe.ql_interpretation_session VALUES ('s1','alice',25,'updated') ON DUPLICATE KEY UPDATE user_id=VALUES(user_id),created_at=VALUES(created_at),payload=VALUES(payload)");
  s.read("session.user_order", "SELECT payload FROM ql_ios_probe.ql_interpretation_session WHERE user_id='alice' ORDER BY created_at DESC,session_id", {{"s:updated"},{"s:new"}});
  s.write("chunk.insert", "INSERT INTO ql_ios_probe.ql_interpretation_chunk VALUES ('s1',2,'two'),('s1',1,'one')", 2);
  s.write("chunk.upsert", "INSERT INTO ql_ios_probe.ql_interpretation_chunk VALUES ('s1',1,'ONE') ON DUPLICATE KEY UPDATE payload=VALUES(payload)");
  s.read("chunk.order", "SELECT payload FROM ql_ios_probe.ql_interpretation_chunk WHERE session_id='s1' ORDER BY sequence", {{"s:ONE"},{"s:two"}});
  s.read("chunk.lookup", "SELECT payload FROM ql_ios_probe.ql_interpretation_chunk WHERE session_id='s1' AND sequence=2", {{"s:two"}});
  s.read("session.wrong_owner", "SELECT session_id FROM ql_ios_probe.ql_interpretation_session WHERE session_id='s1' AND user_id='bob'", {});
  s.begin("session.delete_begin");
  s.read("session.owner", "SELECT session_id FROM ql_ios_probe.ql_interpretation_session WHERE session_id='s1' AND user_id='alice'", {{"s:s1"}});
  s.write("session.delete_chunks", "DELETE FROM ql_ios_probe.ql_interpretation_chunk WHERE session_id='s1'", 2);
  s.write("session.delete_parent", "DELETE FROM ql_ios_probe.ql_interpretation_session WHERE session_id='s1' AND user_id='alice'", 1);
  s.end("session.delete_commit", true);
  s.read("session.deleted", "SELECT session_id FROM ql_ios_probe.ql_interpretation_chunk WHERE session_id='s1'", {});
  s.read("session.other_preserved", "SELECT payload FROM ql_ios_probe.ql_interpretation_session WHERE user_id='bob'", {{"s:other"}});
}

/** Exercise binary audio, locked version reads, metadata updates and speech-preference upserts. */
void listening_cases(Suite &s)
{
  s.write("listening.insert", "INSERT INTO ql_ios_probe.ql_listening VALUES ('alice','audio',1,'{\"title\":\"中文\"}',X'00017FFF'),('bob','audio',1,'{}',X'02')", 2);
  s.read("listening.audio", "SELECT HEX(audio) FROM ql_ios_probe.ql_listening WHERE owner_id='alice' AND material_id='audio'", {{"s:00017FFF"}});
  s.begin("listening.begin");
  s.read("listening.lock_version", "SELECT version FROM ql_ios_probe.ql_listening WHERE owner_id='alice' AND material_id='audio' FOR UPDATE", {{"i:1"}});
  s.write("listening.update", "UPDATE ql_ios_probe.ql_listening SET version=2,payload='{\"title\":\"updated\"}' WHERE owner_id='alice' AND material_id='audio'", 1);
  s.end("listening.commit", true);
  s.read("listening.list", "SELECT material_id,version,JSON_UNQUOTE(JSON_EXTRACT(payload,'$.title')) FROM ql_ios_probe.ql_listening WHERE owner_id='alice' ORDER BY material_id", {{"s:audio","i:2","s:updated"}});
  s.write("speech.upsert", "INSERT INTO ql_ios_probe.ql_listening VALUES ('alice','audio',1,'{\"title\":\"speech\"}',X'') ON DUPLICATE KEY UPDATE payload=VALUES(payload),version=version+1");
  s.read("speech.audio_preserved", "SELECT version,HEX(audio) FROM ql_ios_probe.ql_listening WHERE owner_id='alice' AND material_id='audio'", {{"i:3","s:00017FFF"}});
  s.write("listening.delete", "DELETE FROM ql_ios_probe.ql_listening WHERE owner_id='alice' AND material_id='audio'", 1);
  s.read("listening.other_preserved", "SELECT owner_id,HEX(audio) FROM ql_ios_probe.ql_listening", {{"s:bob","s:02"}});
}

/** Validate review idempotency, conditional versions and event/state transaction atomicity. */
void review_cases(Suite &s)
{
  s.write("review.initialize", "INSERT IGNORE INTO ql_ios_probe.ql_review_state VALUES ('card',0,'initial')", 1);
  s.write("review.idempotent", "INSERT IGNORE INTO ql_ios_probe.ql_review_state VALUES ('card',0,'discard')", 0);
  s.begin("review.begin");
  s.read("review.lock", "SELECT payload FROM ql_ios_probe.ql_review_state WHERE card_id='card' FOR UPDATE", {{"s:initial"}});
  s.read("review.event_absent", "SELECT payload FROM ql_ios_probe.ql_review_event WHERE event_id='event'", {});
  s.write("review.event", "INSERT INTO ql_ios_probe.ql_review_event VALUES ('event','card','committed')", 1);
  s.write("review.version_update", "UPDATE ql_ios_probe.ql_review_state SET version=1,payload='next' WHERE card_id='card' AND version=0", 1);
  s.end("review.commit", true);
  s.read("review.event_readback", "SELECT payload FROM ql_ios_probe.ql_review_event WHERE event_id='event'", {{"s:committed"}});
  s.write("review.duplicate_event_rejected", "INSERT INTO ql_ios_probe.ql_review_event VALUES ('event','card','duplicate')", -1, OB_ERR_PRIMARY_KEY_DUPLICATE);
  s.write("review.stale_version", "UPDATE ql_ios_probe.ql_review_state SET version=2,payload='wrong' WHERE card_id='card' AND version=0", 0);
  s.begin("review.rollback_begin");
  s.write("review.rollback_event", "INSERT INTO ql_ios_probe.ql_review_event VALUES ('rollback','card','discard')", 1);
  s.write("review.rollback_state", "UPDATE ql_ios_probe.ql_review_state SET version=2,payload='discard' WHERE card_id='card' AND version=1", 1);
  s.end("review.rollback", false);
  s.read("review.rollback_no_event", "SELECT payload FROM ql_ios_probe.ql_review_event WHERE event_id='rollback'", {});
  s.read("review.rollback_state_restored", "SELECT version,payload FROM ql_ios_probe.ql_review_state WHERE card_id='card'", {{"i:1","s:next"}});
}

/** Cover native string arrays and the dictionary import's IN and keyset queries. */
void word_cases(Suite &s)
{
  s.write("word.batch", "INSERT INTO ql_ios_probe.ql_word (spelling,meaning,extra_examples,created_at,updated_at) VALUES ('Apple','苹果','[]',1,1),('apple','苹果小写',NULL,1,1),('zebra','斑马','[]',1,1)", 3);
  s.read("word.in", "SELECT spelling FROM ql_ios_probe.ql_word WHERE spelling IN ('Apple','missing')", {{"s:Apple"}});
  s.read("word.keyset", "SELECT spelling FROM ql_ios_probe.ql_word WHERE spelling>'Apple' ORDER BY spelling LIMIT 100", {{"s:apple"},{"s:zebra"}});
  s.read("word.defaults", "SELECT language,version,example_generated,user_modified FROM ql_ios_probe.ql_word WHERE spelling='Apple'", {{"s:en","i:1","i:0","i:0"}});
  s.write("book.insert", "INSERT INTO ql_ios_probe.wordbook (id,title,words) VALUES ('book','中文词书',CONVERT(X'5b224170706c65222c226170706c65225d' USING utf8mb4)),('empty','Empty','[]')", 2);
  s.read("book.array_readback", "SELECT array_to_string(words,CONVERT(X'1f' USING utf8mb4),CONVERT(X'00' USING utf8mb4)) FROM ql_ios_probe.wordbook WHERE id='book'", {{std::string("s:Apple") + char(31) + "apple"}});
  s.read("book.keyset", "SELECT id,array_to_string(words,CONVERT(X'1f' USING utf8mb4),CONVERT(X'00' USING utf8mb4)) FROM ql_ios_probe.wordbook WHERE id>'book' ORDER BY id LIMIT 1", {{"s:empty","s:"}});
  s.read("word.null_and_json", "SELECT COUNT(*) FROM ql_ios_probe.ql_word WHERE extra_examples IS NULL OR JSON_TYPE(extra_examples)='ARRAY'", {{"i:3"}});
  s.write("word.version_constraint", "UPDATE ql_ios_probe.ql_word SET version=0 WHERE spelling='Apple'", -1, OB_ERR_CHECK_CONSTRAINT_VIOLATED);
  s.write("word.example_constraint", "UPDATE ql_ios_probe.ql_word SET example='unpaired' WHERE spelling='Apple'", -1, OB_ERR_CHECK_CONSTRAINT_VIOLATED);
  s.write("word.json_array_constraint", "UPDATE ql_ios_probe.ql_word SET extra_examples='{}' WHERE spelling='Apple'", -1, OB_ERR_CHECK_CONSTRAINT_VIOLATED);
}
}

/** Run the fixture suite with worker context and preserve each completed step on disk. */
int seekdb_ios_probe_quicklang(const char *report_path)
{
  if (report_path == nullptr || report_path[0] != '/' || seekdb_ios_get_state() != SEEKDB_IOS_RUNNING) {
    return OB_INVALID_ARGUMENT;
  }
  FILE *report = std::fopen(report_path, "w");
  if (report == nullptr) { return OB_IO_ERROR; }
  lib::ObStackHeaderGuard stack_header;
  lib::Worker worker;
  lib::Worker::set_worker_to_thread_local(&worker);
  int ret = OB_ERR_UNEXPECTED;
  SERVER_MODULE_SCOPE {
    Suite suite(observer::ObServer::get_instance().get_mysql_proxy(), report);
    schema_cases(suite);
    settings_cases(suite);
    interpretation_cases(suite);
    listening_cases(suite);
    review_cases(suite);
    word_cases(suite);
    ret = suite.result();
  }
  lib::Worker::set_worker_to_thread_local(nullptr);
  if (std::fprintf(report, "{\"complete\":true,\"result\":%d}\n", ret) < 0 && ret == 0) { ret = OB_IO_ERROR; }
  if (std::fclose(report) != 0 && ret == 0) { ret = OB_IO_ERROR; }
  return ret;
}
