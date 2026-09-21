// Copyright (c) 2026 OceanBase.
// SPDX-License-Identifier: Apache-2.0
#import <UIKit/UIKit.h>
#include "seekdb_ios.h"
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
@property(nonatomic) BOOL sqlStarted;
@end

@implementation ProbeDelegate
/** Create the foreground probe and start the engine on a dedicated thread. */
- (void)scene:(UIScene *)scene willConnectToSession:(UISceneSession *)session options:(UISceneConnectionOptions *)options
{
  self.documents = NSSearchPathForDirectoriesInDomains(NSDocumentDirectory, NSUserDomainMask, YES).firstObject;
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

/** Run once, reporting the engine return code back on the UI thread. */
- (void)runEngine
{
  @autoreleasepool {
    NSString *directory = [self.documents stringByAppendingPathComponent:self.dataName];
    int result = seekdb_ios_run(directory.fileSystemRepresentation);
    NSLog(@"seekdb_ios_run returned %d", result);
    dispatch_async(dispatch_get_main_queue(), ^{
      self.result = @(result);
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
    int result = seekdb_ios_probe_sql(&previous);
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
  if (state == SEEKDB_IOS_RUNNING && !self.sqlStarted) {
    self.sqlStarted = YES;
    NSThread *thread = [[NSThread alloc] initWithTarget:self selector:@selector(verifySQL) object:nil];
    thread.stackSize = 8 * 1024 * 1024;
    [thread start];
  }
  NSArray *names = @[@"Idle", @"Starting", @"Running", @"Stopping", @"Stopped", @"Failed"];
  NSString *name = state >= 0 && state < (NSInteger)names.count ? names[state] : @"Unknown";
  self.statusLabel.text = [NSString stringWithFormat:@"seekdb iOS probe\n%@\nEngine: %@\nSQL: %@\nPrevious runs: %@",
                          name, self.result ?: @"pending", self.sqlResult ?: @"pending", self.previousRuns ?: @"pending"];
  NSDictionary *status = @{@"state": name, @"result": self.result ?: NSNull.null, @"data_name": self.dataName,
                           @"sql_verified": @(self.sqlResult != nil && self.sqlResult.intValue == 0),
                           @"sql_result": self.sqlResult ?: NSNull.null,
                           @"previous_runs": self.previousRuns ?: NSNull.null,
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
