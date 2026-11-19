#import <AppKit/AppKit.h>

@interface QuizLensDelegate : NSObject <NSApplicationDelegate>
@property(strong) NSStatusItem *status;
@property(strong) NSTask *server;
@property(strong) NSURL *logURL;
@property BOOL quitting;
@end

@implementation QuizLensDelegate

- (void)openQuiz:(id)sender {
    NSWorkspace *workspace = [NSWorkspace sharedWorkspace];
    NSURL *url = [NSURL URLWithString:@"http://127.0.0.1:8765"];
    // Match app.open_ui so the user's selections stay in the same browser profile.
    for (NSString *identifier in @[@"com.google.Chrome", @"com.microsoft.edgemac"]) {
        NSURL *browser = [workspace URLForApplicationWithBundleIdentifier:identifier];
        if (browser) {
            [workspace openURLs:@[url] withApplicationAtURL:browser
                  configuration:[NSWorkspaceOpenConfiguration configuration]
              completionHandler:^(NSRunningApplication *openedApp, NSError *error) {
                if (error) [workspace openURL:url];
            }];
            return;
        }
    }
    [workspace openURL:url];
}

- (BOOL)applicationShouldHandleReopen:(NSApplication *)app hasVisibleWindows:(BOOL)visible {
    [self openQuiz:nil];
    return YES;
}

- (void)showFailure:(NSString *)message {
    [NSApp activateIgnoringOtherApps:YES];
    NSAlert *alert = [[NSAlert alloc] init];
    alert.messageText = @"Не удалось запустить QuizLens";
    alert.informativeText = message;
    [alert runModal];
}

- (void)applicationDidFinishLaunching:(NSNotification *)note {
    self.status = [[NSStatusBar systemStatusBar] statusItemWithLength:NSVariableStatusItemLength];
    self.status.button.title = @"QuizLens";
    NSMenu *menu = [[NSMenu alloc] init];
    [menu addItemWithTitle:@"Открыть QuizLens" action:@selector(openQuiz:) keyEquivalent:@""].target = self;
    [menu addItem:[NSMenuItem separatorItem]];
    [menu addItemWithTitle:@"Завершить QuizLens" action:@selector(terminate:) keyEquivalent:@"q"].target = NSApp;
    self.status.menu = menu;

    NSURL *resource = [[NSBundle mainBundle].resourceURL URLByAppendingPathComponent:@"quiz-lens"];
    NSFileManager *files = [NSFileManager defaultManager];
    NSURL *library = [files URLsForDirectory:NSLibraryDirectory inDomains:NSUserDomainMask].firstObject;
    NSURL *logs = [library URLByAppendingPathComponent:@"Logs/QuizLens" isDirectory:YES];
    NSError *error = nil;
    self.logURL = [logs URLByAppendingPathComponent:@"launcher.log"];
    if (![files createDirectoryAtURL:logs withIntermediateDirectories:YES attributes:nil error:&error] ||
        ![[NSData data] writeToURL:self.logURL options:NSDataWritingAtomic error:&error]) {
        [self showFailure:error.localizedDescription];
        [NSApp terminate:nil];
        return;
    }
    NSFileHandle *log = [NSFileHandle fileHandleForWritingAtPath:self.logURL.path];
    if (!log) {
        [self showFailure:@"Проверь доступ к папке ~/Library/Logs/QuizLens."];
        [NSApp terminate:nil];
        return;
    }
    self.server = [[NSTask alloc] init];
    self.server.executableURL = [NSURL fileURLWithPath:@"/bin/bash"];
    self.server.arguments = @[[resource URLByAppendingPathComponent:@"launch.command"].path];
    self.server.currentDirectoryURL = resource;
    self.server.standardInput = [NSFileHandle fileHandleWithNullDevice];
    self.server.standardOutput = log;
    self.server.standardError = log;
    __weak QuizLensDelegate *weakSelf = self;
    self.server.terminationHandler = ^(NSTask *task) {
        dispatch_async(dispatch_get_main_queue(), ^{
            QuizLensDelegate *owner = weakSelf;
            if (!owner) return;
            if (owner.quitting) {
                [NSApp replyToApplicationShouldTerminate:YES];
                return;
            }
            if (task.terminationStatus != 0) {
                NSFileHandle *details = [NSFileHandle fileHandleForReadingAtPath:owner.logURL.path];
                unsigned long long end = [details seekToEndOfFile];
                [details seekToFileOffset:end > 2000 ? end - 2000 : 0];
                NSString *reason = [[NSString alloc] initWithData:[details readDataToEndOfFile]
                                                          encoding:NSUTF8StringEncoding];
                [owner showFailure:reason.length ? reason : @"Подробности в ~/Library/Logs/QuizLens/launcher.log."];
            }
            [NSApp terminate:nil];
        });
    };
    if (![self.server launchAndReturnError:&error]) {
        [self showFailure:error.localizedDescription];
        [NSApp terminate:nil];
    }
}

- (NSApplicationTerminateReply)applicationShouldTerminate:(NSApplication *)sender {
    if (!self.server.running) return NSTerminateNow;
    if (!self.quitting) {
        self.quitting = YES;
        [self.server interrupt];
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 4 * NSEC_PER_SEC), dispatch_get_main_queue(), ^{
            if (self.server.running) [self.server terminate];
        });
    }
    return NSTerminateLater;
}
@end

int main(void) {
    @autoreleasepool {
        NSApplication *app = [NSApplication sharedApplication];
        __attribute__((objc_precise_lifetime)) QuizLensDelegate *delegate = [[QuizLensDelegate alloc] init];
        app.delegate = delegate;
        [app setActivationPolicy:NSApplicationActivationPolicyAccessory];
        [app run];
        app.delegate = nil;
        (void)delegate;
    }
    return 0;
}
