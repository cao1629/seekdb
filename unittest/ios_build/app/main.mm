// Copyright (c) 2026 OceanBase.
// SPDX-License-Identifier: Apache-2.0
#import <UIKit/UIKit.h>
#include "seekdb_ios.h"
#include "../device_test_registry.h"
#include "../sql_probe.h"

/** Host one engine lifecycle and persist observable status inside the sandbox. */
@interface ProbeDelegate : UIResponder <UIWindowSceneDelegate>
@property(nonatomic, strong) UIWindow *window;
@property(nonatomic, strong) UILabel *statusLabel;
@property(nonatomic, strong) NSTimer *timer;
@property(nonatomic, copy) NSString *documents;
@property(nonatomic, copy) NSString *dataName;
@property(nonatomic, strong) NSNumber *result;
@property(nonatomic, strong) NSNumber *sqlResult;
@property(nonatomic, strong) NSNumber *previousRuns;
@property(nonatomic, strong) NSNumber *cleanupStatus;
@property(nonatomic, strong) NSNumber *cleanupError;
@property(nonatomic, strong) NSNumber *workingDirectoryRestored;
@property(nonatomic, copy) NSString *initialWorkingDirectory;
@property(nonatomic, copy) NSString *runID;
@property(nonatomic, copy) NSString *testSuite;
@property(nonatomic, copy) NSString *testFilter;
@property(nonatomic, strong) NSNumber *suiteResult;
@property(nonatomic) BOOL verificationStarted;
@end

@implementation ProbeDelegate
/** Create the foreground probe and start the engine on a dedicated thread. */
- (void)scene:(UIScene *)scene willConnectToSession:(UISceneSession *)session options:(UISceneConnectionOptions *)options
{
  self.documents = NSSearchPathForDirectoriesInDomains(NSDocumentDirectory, NSUserDomainMask, YES).firstObject;
  self.initialWorkingDirectory = NSFileManager.defaultManager.currentDirectoryPath;
  self.runID = NSProcessInfo.processInfo.environment[@"SEEKDB_IOS_TEST_RUN_ID"] ?: @"ordinary-run";
  self.testSuite = NSProcessInfo.processInfo.environment[@"SEEKDB_IOS_TEST_SUITE"];
  self.testFilter = NSProcessInfo.processInfo.environment[@"SEEKDB_IOS_TEST_FILTER"] ?: @"*";
  NSString *requestedName = NSProcessInfo.processInfo.environment[@"SEEKDB_PROBE_DATA_NAME"];
  NSCharacterSet *invalid = [[NSCharacterSet characterSetWithCharactersInString:
      @"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"] invertedSet];
  self.dataName = requestedName.length > 0 && requestedName.length <= 64 &&
      [requestedName rangeOfCharacterFromSet:invalid].location == NSNotFound ? requestedName : @"seekdb";
  self.window = [[UIWindow alloc] initWithWindowScene:(UIWindowScene *)scene];
  UIViewController *controller = [UIViewController new];
  controller.view.backgroundColor = UIColor.systemBackgroundColor;
  self.statusLabel = [UILabel new];
  self.statusLabel.numberOfLines = 0;
  self.statusLabel.textAlignment = NSTextAlignmentCenter;
  UIButton *stop = [UIButton buttonWithType:UIButtonTypeSystem];
  [stop setTitle:@"Stop engine" forState:UIControlStateNormal];
  [stop addTarget:self action:@selector(stopEngine) forControlEvents:UIControlEventTouchUpInside];
  UIStackView *stack = [[UIStackView alloc] initWithArrangedSubviews:@[self.statusLabel, stop]];
  stack.axis = UILayoutConstraintAxisVertical;
  stack.spacing = 24;
  stack.translatesAutoresizingMaskIntoConstraints = NO;
  [controller.view addSubview:stack];
  [NSLayoutConstraint activateConstraints:@[
    [stack.leadingAnchor constraintEqualToAnchor:controller.view.safeAreaLayoutGuide.leadingAnchor constant:24],
    [stack.trailingAnchor constraintEqualToAnchor:controller.view.safeAreaLayoutGuide.trailingAnchor constant:-24],
    [stack.centerYAnchor constraintEqualToAnchor:controller.view.centerYAnchor]]];
  self.window.rootViewController = controller;
  [self.window makeKeyAndVisible];
  [self refreshStatus];
  self.timer = [NSTimer scheduledTimerWithTimeInterval:1 target:self selector:@selector(refreshStatus)
                                           userInfo:nil repeats:YES];
  NSThread *thread = [[NSThread alloc] initWithTarget:self selector:@selector(runEngine) object:nil];
  thread.name = @"seekdb-ios-probe";
  thread.stackSize = 8 * 1024 * 1024;
  [thread start];
}

/** Run the explicitly selected device suite and persist run-scoped JSONL evidence. */
- (void)verifyDeviceSuite
{
  @autoreleasepool {
    NSCharacterSet *invalid = [[NSCharacterSet characterSetWithCharactersInString:
        @"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-"] invertedSet];
    BOOL validRunID = self.runID.length > 0 && self.runID.length <= 64 &&
        [self.runID rangeOfCharacterFromSet:invalid].location == NSNotFound;
    int result = 2;
    if (validRunID) {
      NSString *name = [NSString stringWithFormat:@"device-test-%@.jsonl", self.runID];
      NSString *report = [self.documents stringByAppendingPathComponent:name];
      seekdb::ios_test::DeviceTestRegistry registry = seekdb::ios_test::make_smoke_registry();
      result = seekdb::ios_test::run_device_suite(
          registry, self.testSuite.UTF8String, self.testFilter.UTF8String, self.runID.UTF8String,
          seekdb_ios_get_build_id(), report.fileSystemRepresentation);
    }
    NSLog(@"Device suite returned %d", result);
    dispatch_async(dispatch_get_main_queue(), ^{
      self.suiteResult = @(result);
      [self refreshStatus];
      if ([NSProcessInfo.processInfo.environment[@"SEEKDB_PROBE_AUTO_STOP"] isEqualToString:@"1"]) {
        [self stopEngine];
      }
    });
  }
}

/** Run once, reporting the engine return code back on the UI thread. */
- (void)runEngine
{
  @autoreleasepool {
    NSString *directory = [self.documents stringByAppendingPathComponent:self.dataName];
    int result = seekdb_ios_run(directory.fileSystemRepresentation);
    unsigned int cleanupStatus = seekdb_ios_get_cleanup_status();
    int cleanupError = seekdb_ios_get_cleanup_error();
    BOOL workingDirectoryRestored =
        [NSFileManager.defaultManager.currentDirectoryPath isEqualToString:self.initialWorkingDirectory];
    NSLog(@"seekdb_ios_run returned %d", result);
    dispatch_async(dispatch_get_main_queue(), ^{
      self.result = @(result);
      self.cleanupStatus = @(cleanupStatus);
      self.cleanupError = @(cleanupError);
      self.workingDirectoryRestored = @(workingDirectoryRestored);
      [self refreshStatus];
    });
  }
}

/** Request a graceful stop without blocking the main thread. */
- (void)stopEngine
{
  seekdb_ios_request_stop();
}

/** Exercise the internal SQL client off the main thread and optionally request shutdown. */
- (void)verifySQL
{
  @autoreleasepool {
    int64_t previous = 0;
    NSString *report = [self.documents stringByAppendingPathComponent:@"sql-probe-results.jsonl"];
    int result = seekdb_ios_probe_sql(report.fileSystemRepresentation, &previous);
    NSLog(@"SQL probe returned %d, previous runs %lld", result, (long long)previous);
    dispatch_async(dispatch_get_main_queue(), ^{
      self.sqlResult = @(result);
      self.previousRuns = result == 0 ? @(previous) : nil;
      [self refreshStatus];
      if ([NSProcessInfo.processInfo.environment[@"SEEKDB_PROBE_AUTO_STOP"] isEqualToString:@"1"]) {
        [self stopEngine];
      }
    });
  }
}

/** Write lifecycle evidence; running alone does not establish SQL correctness. */
- (void)refreshStatus
{
  NSInteger state = seekdb_ios_get_state();
  UIApplication.sharedApplication.idleTimerDisabled =
      state != SEEKDB_IOS_STOPPED && state != SEEKDB_IOS_FAILED;
  if (state == SEEKDB_IOS_RUNNING && !self.verificationStarted) {
    self.verificationStarted = YES;
    SEL selector = self.testSuite.length > 0 ? @selector(verifyDeviceSuite) : @selector(verifySQL);
    NSThread *thread = [[NSThread alloc] initWithTarget:self selector:selector object:nil];
    thread.stackSize = 8 * 1024 * 1024;
    [thread start];
  }
  NSArray *names = @[@"Idle", @"Starting", @"Running", @"Stopping", @"Stopped", @"Failed"];
  NSString *name = state >= 0 && state < (NSInteger)names.count ? names[state] : @"Unknown";
  self.statusLabel.text = [NSString stringWithFormat:@"seekdb iOS probe\n%@\nEngine: %@\nSQL: %@\nSuite: %@\nPrevious runs: %@",
                          name, self.result ?: @"pending", self.sqlResult ?: @"pending",
                          self.suiteResult ?: @"not selected", self.previousRuns ?: @"pending"];
  NSDictionary *status = @{@"state": name, @"result": self.result ?: NSNull.null, @"data_name": self.dataName,
                           @"build_id": [NSString stringWithUTF8String:seekdb_ios_get_build_id()],
                           @"hook_mode": [NSString stringWithUTF8String:seekdb_ios_get_hook_mode()],
                           @"sql_verified": @(self.sqlResult != nil && self.sqlResult.intValue == 0),
                           @"sql_result": self.sqlResult ?: NSNull.null,
                           @"suite_result": self.suiteResult ?: NSNull.null,
                           @"previous_runs": self.previousRuns ?: NSNull.null,
                           @"cleanup_status": self.cleanupStatus ?: NSNull.null,
                           @"cleanup_error": self.cleanupError ?: NSNull.null,
                           @"working_directory_restored": self.workingDirectoryRestored ?: NSNull.null,
                           @"run_id": self.runID,
                           @"timestamp": @([[NSDate date] timeIntervalSince1970])};
  NSError *error = nil;
  NSData *data = [NSJSONSerialization dataWithJSONObject:status options:NSJSONWritingPrettyPrinted error:&error];
  if (data != nil && ![data writeToFile:[self.documents stringByAppendingPathComponent:@"probe-status.json"]
                              options:NSDataWritingAtomic error:&error]) {
    NSLog(@"Cannot save probe status: %@", error);
  }
}
@end

/** Let UIKit create the single window scene declared in the application manifest. */
@interface ProbeApplication : UIResponder <UIApplicationDelegate>
@end
@implementation ProbeApplication
@end

/** Enter UIKit; the app delegate owns the background engine thread. */
int main(int argc, char **argv)
{
  @autoreleasepool {
    return UIApplicationMain(argc, argv, nil, NSStringFromClass(ProbeApplication.class));
  }
}
