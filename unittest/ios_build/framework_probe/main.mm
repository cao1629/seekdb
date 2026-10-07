// Copyright (c) 2026 OceanBase.
// SPDX-License-Identifier: Apache-2.0
#import <UIKit/UIKit.h>
#include <dlfcn.h>
#include <cstring>
#include <unistd.h>
#include <pthread.h>
#include "seekdb.h"
#include "seekdb_ios.h"

#include "driver.h"
#include "stability.h"

/** Retain a test report that is atomically updated after each completed operation. */
class Probe {
public:
  /** Bind the report to the App's Documents directory. */
  Probe() : report_([NSMutableDictionary dictionary]), steps_([NSMutableArray array])
  {
    NSString *documents = NSSearchPathForDirectoriesInDomains(NSDocumentDirectory, NSUserDomainMask, YES).firstObject;
    [NSFileManager.defaultManager createDirectoryAtPath:documents withIntermediateDirectories:YES attributes:nil error:nil];
    path_ = [documents stringByAppendingPathComponent:@"framework-probe.json"];
    report_[@"steps"] = steps_;
    report_[@"complete"] = @NO;
    report_[@"probe_source_revision"] = @PROBE_SOURCE_REVISION;
    report_[@"run_id"] = NSUUID.UUID.UUIDString;
    report_[@"test_transport"] = @"Connector/C over actual Unix socket";
    save();
  }

  /** Record one assertion and preserve the first failure. */
  bool check(NSString *name, bool success)
  {
    [steps_ addObject:@{@"name": name, @"passed": @(success)}];
    if (!success) {
      report_[@"failed"] = name;
      report_[@"posix_errno"] = @(errno);
      report_[@"home"] = NSHomeDirectory();
      const char *environment_home = getenv("HOME");
      report_[@"environment_home"] = environment_home ? [NSString stringWithUTF8String:environment_home] : @"missing";
      if (driver_.seekdb_ios_get_state != nullptr) {
        report_[@"engine_state"] = @(driver_.seekdb_ios_get_state());
        report_[@"cleanup_error"] = @(driver_.seekdb_ios_get_cleanup_error());
      }
      if (connection_ != nullptr && driver_.seekdb_last_error != nullptr) {
        int number = 0;
        const char *message = nullptr;
        driver_.seekdb_last_error(connection_, &number, &message);
        report_[@"mysql_errno"] = @(number);
        report_[@"mysql_error"] = message ? [NSString stringWithUTF8String:message] : @"";
      }
    }
    save();
    return success;
  }

  /** Execute SQL through the same C API used by the macOS Rust native driver. */
  bool query(const char *sql, SeekdbResult *result)
  {
    return check([NSString stringWithUTF8String:sql],
      driver_.seekdb_query(connection_, sql, strlen(sql), result) == SEEKDB_SUCCESS);
  }

  /** Execute a statement without leaking its allocated empty result. */
  bool statement(const char *sql)
  {
    SeekdbResult result = nullptr;
    if (!query(sql, &result)) {
      return false;
    }
    return check(@"free statement result", driver_.seekdb_result_free(result) == SEEKDB_SUCCESS);
  }

  /** Read a scalar and enforce complete result traversal. */
  bool scalar(const char *sql, int64_t *value)
  {
    SeekdbResult result = nullptr;
    if (!query(sql, &result)) {
      return false;
    }
    bool success = driver_.seekdb_result_next(result) == SEEKDB_SUCCESS
      && driver_.seekdb_result_get_int64(result, 0, value) == SEEKDB_SUCCESS
      && driver_.seekdb_result_next(result) == SEEKDB_NO_MORE_ROWS;
    driver_.seekdb_result_free(result);
    return check(@"scalar traversal", success);
  }

  /** Validate loading, SQL, transactions, nullable UTF-8 and process restart persistence. */
  void run()
  {
    NSString *binary = [NSBundle.mainBundle.privateFrameworksPath stringByAppendingPathComponent:@"SeekDB.framework/SeekDB"];
    library_ = dlopen(binary.fileSystemRepresentation, RTLD_NOW | RTLD_LOCAL);
    if (!check(@"dlopen embedded framework", library_ != nullptr)) {
      const char *error = dlerror();
      report_[@"loader_error"] = error ? [NSString stringWithUTF8String:error] : @"unknown";
      save();
      return;
    }
#define LOAD(name) driver_.name = reinterpret_cast<decltype(driver_.name)>(dlsym(library_, #name)); \
    if (!check(@"dlsym " #name, driver_.name != nullptr)) return;
    LOAD(seekdb_open) LOAD(seekdb_close) LOAD(seekdb_connection_options)
    LOAD(seekdb_connect) LOAD(seekdb_disconnect) LOAD(seekdb_last_error)
    LOAD(seekdb_query) LOAD(seekdb_result_free) LOAD(seekdb_result_column_count)
    LOAD(seekdb_result_column_name) LOAD(seekdb_result_column_type_id)
    LOAD(seekdb_result_row_count) LOAD(seekdb_result_next)
    LOAD(seekdb_result_get_int64) LOAD(seekdb_result_get_uint64)
    LOAD(seekdb_result_get_float) LOAD(seekdb_result_get_str)
    LOAD(seekdb_trx_begin) LOAD(seekdb_trx_commit) LOAD(seekdb_trx_rollback)
    LOAD(seekdb_value_free) LOAD(seekdb_value_create_int64) LOAD(seekdb_value_get_int64)
    LOAD(seekdb_malloc) LOAD(seekdb_free)
    LOAD(seekdb_ios_get_state) LOAD(seekdb_ios_get_cleanup_status)
    LOAD(seekdb_ios_get_cleanup_error) LOAD(seekdb_ios_get_build_id) LOAD(seekdb_ios_get_hook_mode)
#undef LOAD
    report_[@"build_id"] = [NSString stringWithUTF8String:driver_.seekdb_ios_get_build_id()];
    report_[@"hook_mode"] = [NSString stringWithUTF8String:driver_.seekdb_ios_get_hook_mode()];
    if (!check(@"production hooks disabled", strcmp(driver_.seekdb_ios_get_hook_mode(), "disabled") == 0)) return;
    NSString *base = [NSHomeDirectory() stringByAppendingPathComponent:@"Documents/framework-db"];
    char before[4096];
    if (!check(@"capture cwd", getcwd(before, sizeof(before)) != nullptr)) return;
    const char *invalid[] = {"port", "3306", nullptr};
    SeekdbHandle bad = nullptr;
    if (!check(@"reject TCP", driver_.seekdb_open(base.fileSystemRepresentation, invalid, &bad) == SEEKDB_INVALID_ARGUMENT && bad == nullptr)) return;
    if (!check(@"open engine", driver_.seekdb_open(base.fileSystemRepresentation, nullptr, &handle_) == SEEKDB_SUCCESS)) return;
    SeekdbConnectionOptions options{};
    if (!check(@"root Unix socket options", driver_.seekdb_connection_options(handle_, &options) == SEEKDB_SUCCESS
        && strcmp(options.transport, "unix_socket") == 0 && strcmp(options.user, "root") == 0
        && options.endpoint != nullptr && strlen(options.endpoint) < 104
        && options.port == 0)) { finish(false, before); return; }
    SeekdbHandle second = nullptr;
    if (!check(@"share existing engine", driver_.seekdb_open(base.fileSystemRepresentation, nullptr, &second) == SEEKDB_SUCCESS)) { finish(false, before); return; }
    if (!check(@"close shared handle", driver_.seekdb_close(second) == SEEKDB_SUCCESS
        && driver_.seekdb_ios_get_state() == SEEKDB_IOS_RUNNING)) { finish(false, before); return; }
    if (!check(@"connect root empty password", driver_.seekdb_connect(handle_, nullptr, true, &connection_) == SEEKDB_SUCCESS)) { finish(false, before); return; }
    bool passed = sql_suite();
    if (passed) {
      passed = run_framework_stability(driver_, handle_, connection_, report_, [this] { save(); });
    }
    finish(passed, before);
  }

private:
  /** Exercise actual engine results and transaction commit/rollback visibility. */
  bool sql_suite()
  {
    if (!statement("CREATE DATABASE IF NOT EXISTS framework_probe")
        || !statement("USE framework_probe")
        || !statement("CREATE TABLE IF NOT EXISTS persistence (id BIGINT PRIMARY KEY, n BIGINT NOT NULL)")
        || !statement("INSERT IGNORE INTO persistence VALUES (1, 0)")) return false;
    int64_t previous = -1;
    if (!scalar("SELECT n FROM persistence WHERE id=1", &previous)) return false;
    report_[@"previous_runs"] = @(previous);
    if (!statement("UPDATE persistence SET n=n+1 WHERE id=1")
        || !statement("DROP TABLE IF EXISTS cells")
        || !statement("CREATE TABLE cells (id BIGINT PRIMARY KEY, v VARCHAR(100) NULL)")
        || !check(@"begin committed transaction", driver_.seekdb_trx_begin(connection_) == 0)
        || !statement("INSERT INTO cells VALUES (1, '中文🙂'), (2, NULL), (3, '')")
        || !check(@"commit transaction", driver_.seekdb_trx_commit(connection_) == 0)
        || !check(@"begin rolled back transaction", driver_.seekdb_trx_begin(connection_) == 0)
        || !statement("INSERT INTO cells VALUES (4, 'rollback')")
        || !check(@"rollback transaction", driver_.seekdb_trx_rollback(connection_) == 0)) return false;
    int64_t count = 0;
    if (!scalar("SELECT COUNT(*) FROM cells", &count) || !check(@"rollback invisible and commit visible", count == 3)) return false;
    SeekdbResult result = nullptr;
    if (!query("SELECT id,v FROM cells ORDER BY id", &result)) return false;
    int64_t columns = 0, rows = 0;
    const char *name = nullptr;
    SeekdbTypeId type = SEEKDB_TYPE_NULL;
    bool passed = driver_.seekdb_result_column_count(result, &columns) == 0 && columns == 2
      && driver_.seekdb_result_row_count(result, &rows) == 0 && rows == 3
      && driver_.seekdb_result_column_name(result, 1, &name) == 0 && strcmp(name, "v") == 0
      && driver_.seekdb_result_column_type_id(result, 1, &type) == 0 && type == SEEKDB_TYPE_VARCHAR;
    const char *expected[] = {"中文🙂", nullptr, ""};
    for (int i = 0; i < 3 && passed; ++i) {
      const char *data = nullptr;
      size_t length = 99;
      int is_null = -1;
      int64_t id = 0;
      passed = driver_.seekdb_result_next(result) == 0
        && driver_.seekdb_result_get_int64(result, 0, &id) == 0 && id == i + 1
        && driver_.seekdb_result_get_str(result, 1, &data, &length, &is_null) == 0;
      if (expected[i] == nullptr) {
        passed = passed && is_null == 1 && data == nullptr && length == 0;
      } else {
        passed = passed && is_null == 0 && length == strlen(expected[i])
          && data != nullptr && memcmp(data, expected[i], length) == 0;
      }
    }
    passed = passed && driver_.seekdb_result_next(result) == SEEKDB_NO_MORE_ROWS;
    driver_.seekdb_result_free(result);
    if (!check(@"nullable UTF-8, empty string, metadata and row traversal", passed)) return false;
    if (!query("SELECT CAST(18446744073709551615 AS UNSIGNED), 1.25", &result)) return false;
    uint64_t integer = 0;
    double floating = 0;
    passed = driver_.seekdb_result_next(result) == 0
      && driver_.seekdb_result_get_uint64(result, 0, &integer) == 0 && integer == UINT64_MAX
      && driver_.seekdb_result_get_float(result, 1, &floating) == 0 && floating == 1.25;
    driver_.seekdb_result_free(result);
    if (!check(@"unsigned and floating results", passed)) return false;
    SeekdbValue value = nullptr;
    int64_t signed_value = 0;
    passed = driver_.seekdb_value_create_int64(-42, &value) == 0
      && driver_.seekdb_value_get_int64(value, &signed_value) == 0 && signed_value == -42;
    if (value != nullptr) driver_.seekdb_value_free(value);
    if (!check(@"value ownership", passed)) return false;
    void *allocated = driver_.seekdb_malloc(32);
    driver_.seekdb_free(allocated);
    if (!check(@"framework allocation", allocated != nullptr)) return false;
    result = nullptr;
    int error = 0;
    const char *message = nullptr;
    return check(@"SQL errors retain MySQL diagnostics", driver_.seekdb_query(connection_, "SELECT missing_column", 21, &result) == SEEKDB_INTERNAL_ERROR
      && result == nullptr && driver_.seekdb_last_error(connection_, &error, &message) == 0
      && error != 0 && message != nullptr && message[0] != '\0');
  }

  /** Close all owned objects and assert restored process state before completing the report. */
  void finish(bool success, const char *before)
  {
    if (connection_ != nullptr) {
      success = check(@"disconnect", driver_.seekdb_disconnect(connection_) == 0) && success;
      connection_ = nullptr;
    }
    success = check(@"close and join engine", driver_.seekdb_close(handle_) == 0) && success;
    handle_ = nullptr;
    char after[4096];
    success = check(@"stopped and cleaned up", driver_.seekdb_ios_get_state() == SEEKDB_IOS_STOPPED
      && driver_.seekdb_ios_get_cleanup_status() == 7 && driver_.seekdb_ios_get_cleanup_error() == 0
      && getcwd(after, sizeof(after)) != nullptr && strcmp(before, after) == 0) && success;
    SeekdbHandle retry = nullptr;
    success = check(@"one startup per process", driver_.seekdb_open("/unused", nullptr, &retry) == SEEKDB_INTERNAL_ERROR
      && retry == nullptr) && success;
    report_[@"complete"] = @YES;
    report_[@"passed"] = @(success);
    save();
    // Keep the library loaded until process exit; unloading an active singleton is unsupported.
  }

  /** Atomically persist progress so a crash cannot turn incomplete work into success. */
  void save()
  {
    NSData *data = [NSJSONSerialization dataWithJSONObject:report_ options:NSJSONWritingPrettyPrinted error:nil];
    [data writeToFile:path_ options:NSDataWritingAtomic error:nil];
  }

  Driver driver_;
  void *library_ = nullptr;
  SeekdbHandle handle_ = nullptr;
  SeekdbConnection connection_ = nullptr;
  NSMutableDictionary *report_;
  NSMutableArray *steps_;
  NSString *path_;
};

/** Execute and release the retained probe block on an explicitly joinable loading thread. */
static void *run_probe_thread(void *context)
{
  @autoreleasepool {
    dispatch_block_t callback = (__bridge_transfer dispatch_block_t)context;
    callback();
  }
  return nullptr;
}

/** Keep the standalone dynamically loaded SQL probe foregrounded during execution. */
@interface FrameworkProbeDelegate : UIResponder <UIWindowSceneDelegate>
@property(nonatomic, strong) UIWindow *window;
@end
@implementation FrameworkProbeDelegate
/** Show a status screen and run the blocking C ABI on a joinable background thread. */
- (void)scene:(UIScene *)scene willConnectToSession:(UISceneSession *)session options:(UISceneConnectionOptions *)options
{
  self.window = [[UIWindow alloc] initWithWindowScene:(UIWindowScene *)scene];
  UIViewController *controller = [[UIViewController alloc] init];
  controller.view.backgroundColor = UIColor.systemBackgroundColor;
  UILabel *label = [[UILabel alloc] initWithFrame:CGRectMake(20, 100, 350, 180)];
  label.numberOfLines = 0;
  label.text = @"SeekDB dynamic framework probe\nRunning SQL over Unix socket…\nKeep this App in the foreground.";
  [controller.view addSubview:label];
  self.window.rootViewController = controller;
  [self.window makeKeyAndVisible];
  UIApplication.sharedApplication.idleTimerDisabled = YES;
  // Preserve a bounded diagnostic tail while the synchronous open is waiting.
  [NSTimer scheduledTimerWithTimeInterval:5 repeats:YES block:^(NSTimer *timer) {
    NSString *documents = NSSearchPathForDirectoriesInDomains(NSDocumentDirectory, NSUserDomainMask, YES).firstObject;
    NSString *log = [documents stringByAppendingPathComponent:@"framework-db/log/seekdb.log"];
    NSFileHandle *stream = [NSFileHandle fileHandleForReadingAtPath:log];
    if (stream != nil) {
      unsigned long long size = [stream seekToEndOfFile];
      [stream seekToFileOffset:size > 32768 ? size - 32768 : 0];
      NSData *tail = [stream readDataOfLength:32768];
      [stream closeFile];
      [tail writeToFile:[documents stringByAppendingPathComponent:@"framework-engine-tail.txt"] atomically:YES];
    }
  }];
  dispatch_block_t callback = ^{
    @autoreleasepool {
      Probe probe;
      probe.run();
      dispatch_async(dispatch_get_main_queue(), ^{ label.text = @"Probe finished.\nRead Documents/framework-probe.json for the result."; });
    }
  };
  pthread_t worker;
  void *retained_callback = (__bridge_retained void *)[callback copy];
  if (pthread_create(&worker, nullptr, run_probe_thread, retained_callback) != 0) {
    dispatch_block_t released = (__bridge_transfer dispatch_block_t)retained_callback;
    (void)released;
    label.text = @"Failed to create the probe thread.";
    return;
  }
  // Joining proves that thread-local allocator destructors survive after engine shutdown.
  dispatch_async(dispatch_get_global_queue(QOS_CLASS_UTILITY, 0), ^{
    if (pthread_join(worker, nullptr) == 0) {
      NSString *documents = NSSearchPathForDirectoriesInDomains(NSDocumentDirectory, NSUserDomainMask, YES).firstObject;
      NSString *path = [documents stringByAppendingPathComponent:@"framework-probe.json"];
      NSMutableDictionary *report = [[NSJSONSerialization JSONObjectWithData:[NSData dataWithContentsOfFile:path]
          options:NSJSONReadingMutableContainers error:nil] mutableCopy];
      if (report != nil) {
        report[@"worker_exit"] = @YES;
        [[NSJSONSerialization dataWithJSONObject:report options:NSJSONWritingPrettyPrinted error:nil]
            writeToFile:path options:NSDataWritingAtomic error:nil];
      }
    }
  });
}
@end

/** Let UIKit create the single probe scene declared in the manifest. */
@interface FrameworkApplicationDelegate : UIResponder <UIApplicationDelegate>
@end
@implementation FrameworkApplicationDelegate
/** Accept launch without starting the database before a foreground scene exists. */
- (BOOL)application:(UIApplication *)application didFinishLaunchingWithOptions:(NSDictionary *)options
{
  return YES;
}
@end

/** Start the independent UIKit probe without linking any seekdb symbols. */
int main(int argc, char **argv)
{
  @autoreleasepool {
    return UIApplicationMain(argc, argv, nil, NSStringFromClass(FrameworkApplicationDelegate.class));
  }
}
