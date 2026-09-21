// Copyright (c) 2026 OceanBase.
// SPDX-License-Identifier: Apache-2.0
#import <UIKit/UIKit.h>
#include "seekdb_ios.h"

/** Host one engine lifecycle and persist observable status inside the sandbox. */
@interface ProbeDelegate : UIResponder <UIApplicationDelegate>
@property(nonatomic, strong) UIWindow *window;
@property(nonatomic, strong) UILabel *statusLabel;
@property(nonatomic, strong) NSTimer *timer;
@property(nonatomic, copy) NSString *documents;
@property(nonatomic, strong) NSNumber *result;
@end

@implementation ProbeDelegate
/** Create the foreground probe and start the engine on a dedicated thread. */
- (BOOL)application:(UIApplication *)application didFinishLaunchingWithOptions:(NSDictionary *)options
{
  self.documents = NSSearchPathForDirectoriesInDomains(NSDocumentDirectory, NSUserDomainMask, YES).firstObject;
  self.window = [[UIWindow alloc] initWithFrame:UIScreen.mainScreen.bounds];
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
  return YES;
}

/** Run once, reporting the engine return code back on the UI thread. */
- (void)runEngine
{
  @autoreleasepool {
    NSString *directory = [self.documents stringByAppendingPathComponent:@"seekdb"];
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

/** Write lifecycle evidence; running alone does not establish SQL correctness. */
- (void)refreshStatus
{
  NSInteger state = seekdb_ios_get_state();
  NSArray *names = @[@"Idle", @"Starting", @"Running", @"Stopping", @"Stopped", @"Failed"];
  NSString *name = state >= 0 && state < (NSInteger)names.count ? names[state] : @"Unknown";
  self.statusLabel.text = [NSString stringWithFormat:@"seekdb iOS probe\n%@\nResult: %@\nSQL validation pending",
                          name, self.result ?: @"pending"];
  NSDictionary *status = @{@"state": name, @"result": self.result ?: NSNull.null,
                           @"sql_verified": @NO, @"timestamp": @([[NSDate date] timeIntervalSince1970])};
  NSError *error = nil;
  NSData *data = [NSJSONSerialization dataWithJSONObject:status options:NSJSONWritingPrettyPrinted error:&error];
  if (data != nil && ![data writeToFile:[self.documents stringByAppendingPathComponent:@"probe-status.json"]
                              options:NSDataWritingAtomic error:&error]) {
    NSLog(@"Cannot save probe status: %@", error);
  }
}
@end

/** Enter UIKit; the app delegate owns the background engine thread. */
int main(int argc, char **argv)
{
  @autoreleasepool {
    return UIApplicationMain(argc, argv, nil, NSStringFromClass(ProbeDelegate.class));
  }
}
