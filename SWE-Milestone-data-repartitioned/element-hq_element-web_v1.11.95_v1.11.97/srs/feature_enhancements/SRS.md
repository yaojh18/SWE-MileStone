# Software Requirements Specification: Trustworthy Identity and Room Interactions

## Overview

Make Element Web preserve user intent across identity state transitions and room interactions. Security-sensitive actions must expose their true pending or verification state and prevent an accidental or premature transition. Room actions must address only the intended people or room, use unambiguous copy, and expose notification state through precise contracts. The resulting flows must follow existing Matrix, React, Compound, and i18n conventions so the interface and its underlying state remain consistent.

### Requirements Summary

1. **FR1**: Guard Identity Reset and Forced Verification Transitions
2. **FR2**: Improve Room Notification State API
3. **FR3**: Pinned Identity Change Dismissal Button Text
4. **FR4**: Remove Unintentional Mentions in Replies
5. **FR5**: Add Room Reporting Dialog

### Affected Modules

- ResetIdentityPanel, MatrixChat forced-verification flow, and UserIdentityWarning
- NotificationState, RoomNotificationState, and RoomNotifs
- SendMessageComposer reply-mention targeting
- RoomSummaryCard, ReportRoomDialog, and their related styles

## Functional Requirements

### Functional Area: Accurate client state and guarded transitions

The client must represent pending identity work, guarded navigation, and room-notification categories according to their underlying state.

### FR1: Guard Identity Reset and Forced Verification Transitions
Security-sensitive identity transitions must expose their pending state and prevent every navigation or repeated-action path that would bypass the required operation.

**Problem**:
Users can click the identity reset "Continue" button multiple times while the reset operation is in progress, potentially triggering concurrent reset operations that could corrupt cryptographic state or cause key loss.

When the application is configured with mandatory device verification (`force_verification` setting enabled), users can bypass the verification requirement by initiating device verification with another device and then canceling the verification dialog without completing it, allowing access to the application without proper verification.

**Requirements**:
- When the user initiates an identity reset operation, the action button must become disabled immediately upon click
- While the reset operation is in progress, visual feedback must clearly indicate that the operation is ongoing
- A spinner or loading indicator must be displayed in place of or alongside the button text
- The button text must change to indicate the operation is in progress (e.g., "Reset in progress...")
- A warning message must be displayed advising the user not to close the browser window while the operation is in progress
- The cancel button must be hidden during the reset operation to prevent user confusion
- The warning message about not closing the window must be styled prominently (using critical/warning coloring)

- When force verification is enabled and the user has not yet verified their device, dismissing or canceling an in-progress device verification dialog must not grant access to the application
- After the e2e/security setup flow completes or is dismissed, the application must check whether cross-signing is ready before allowing the user to proceed
- If force verification is required and the device remains unverified after the security setup flow concludes, the user must remain on the verification prompt screen
- The verification check must be enforced at two distinct entry points to the post-login screen:
  1. The initial post-login navigation that occurs after session setup completes
  2. The callback invoked when security/e2e setup dialogs are closed or dismissed

**Acceptance**:
- When a user clicks the identity reset "Continue" button, the button becomes disabled and shows a loading spinner with progress text
- When the reset operation is in progress, a prominent warning message is displayed telling the user not to close the window
- When the reset operation is in progress, the cancel button is replaced with the warning message
- The reset operation cannot be triggered multiple times by repeated button clicks

- When force verification is enabled and a user cancels device verification after clicking "Verify with another device", the application continues to display the "Verify this device" prompt
- When force verification is enabled and cross-signing is not ready, the application does not show the post-login screen
- When force verification is enabled and the user successfully completes verification (cross-signing becomes ready), the application proceeds normally to the post-login screen
- After the security setup flow completes or is dismissed, the application must check if cross-signing is ready before proceeding to the post-login screen when force verification is active
- The application must not proceed to the post-login screen if cross-signing is not ready while force verification is required
<!-- COMMENTED OUT - Implementation detail:
- Check via `MatrixClientPeg.safeGet().getCrypto()?.isCrossSigningReady()` before calling `onShowPostLoginScreen()`
-->

### FR2: Improve Room Notification State API
**Problem**: The room notification state API has confusing method names and lacks clear documentation. The `hasMentions` property is misleading because it returns true for invitations, knocks, and unsent messages, not just actual mentions.

**Requirements**:
- Add an `invited` property (getter) to `NotificationState` base class (`src/stores/notifications/NotificationState.ts`) with backing field `_invited`
- Add new getter properties to `RoomNotificationState` (`src/stores/notifications/RoomNotificationState.ts`):
  - `isMention: boolean` - Returns true only for actual mention notifications (excludes invitations, knocks, and unsent messages)
  - `isUnsetMessage: boolean` - Returns true when notification level equals `NotificationLevel.Unsent`
  - `isActivityNotification: boolean` - Returns true when notification level equals `NotificationLevel.Activity`
  - `isNotification: boolean` - Returns true when notification level equals `NotificationLevel.Notification`
  - `hasAnyNotificationOrActivity: boolean` - Returns true for any notification or activity (considering `feature_hidebold` setting)
- Deprecate the confusing `hasMentions` property with JSDoc `@deprecated` tag explaining the replacement
- Add JSDoc documentation to existing getters (`isUnread`, `hasUnreadCount`) explaining their behavior
- The `determineUnreadState` function in `src/RoomNotifs.ts` must return an `invited: boolean` field in its result object
- `INotificationStateSnapshotParams` interface must include `invited: boolean` for change detection

**Acceptance**:
- `RoomNotificationState.invited` getter returns true when room membership is `Invite`
- `RoomNotificationState.isUnsetMessage` getter returns true when the notification level is Unsent
- `RoomNotificationState.isMention` getter returns true only for actual mention notifications (excludes invitations, knocks, and unsent messages)
- `RoomNotificationState.isNotification` getter returns true when notification level is Notification
- `RoomNotificationState.isActivityNotification` getter returns true when notification level is Activity
- `RoomNotificationState.hasAnyNotificationOrActivity` returns true for knocks and notification levels at or above `Notification`, and for `Activity` only when `feature_hidebold` is disabled
<!-- COMMENTED OUT - Implementation detail:
- `isMention` returns `false` if `this.invited` or `this.knocked` is true, otherwise returns `this.level === NotificationLevel.Highlight`
- `hasAnyNotificationOrActivity` returns true if: knocked is true, OR (`feature_hidebold` is false AND level is Activity), OR level >= Notification
-->


### Functional Area: Intent-preserving feedback and room actions

Labels, mention targeting, and reporting controls must communicate and execute exactly the action the user selected.

### FR3: Pinned Identity Change Dismissal Button Text
**Problem**: When a user's pinned cryptographic identity changes (as opposed to a verification violation), the notification banner displays "Ok" as the action button text. This is confusing because "Ok" typically implies agreement or confirmation, whereas the user is simply acknowledging and dismissing the notification.

**Requirements**:
- For pinned identity change notifications (non-verification violations), the action button must display "Dismiss" instead of "Ok"
- For verification violation notifications, the existing "Withdraw verification" action text must remain unchanged

**Acceptance**:
- When a pinned identity change notification is displayed, the action button shows "Dismiss"
- When a verification violation notification is displayed, the action button shows "Withdraw verification"


### FR4: Remove Unintentional Mentions in Replies
**Problem**: When users reply to a message, the reply unintentionally forwards all user mentions from the original message to the new reply, causing unwanted notifications to users who were mentioned in the original message but not explicitly mentioned in the reply.

**Requirements**:
- When composing a reply, initialize `m.mentions.user_ids` from the replied-to sender and from mentions explicitly added in the new reply
- User IDs mentioned in the original message's `m.mentions.user_ids` array must not be automatically propagated to the reply
- The composer must not copy mentions from the replied-to event's content
- Only explicit mentions added by the user in the reply text should be included

**Acceptance**:
- When replying to a message sent by @bob that mentions @charlie, include @bob but exclude @charlie unless the new reply explicitly mentions @charlie
- When replying to a message with no explicit mentions in the reply text, only the original sender is mentioned
- Existing behavior for explicit @-mentions typed by the user in the reply body remains unchanged
- Do not inherit user IDs from the replied-to event; room-mention behavior otherwise remains unchanged


### FR5: Add Room Reporting Dialog
**Problem**: Users have no way to report problematic rooms to their homeserver administrator from within the application.

**Requirements**:

#### Room Summary Card Changes
- Add a "Report room" option in the room summary card (right panel)
  - The "Report room" menu item must be placed **after** the existing "Leave room" button
  - Both "Leave room" and "Report room" buttons must be wrapped together in a container element with className `mx_RoomSummaryCard_bottomOptions`
  - The "Report room" button must use `kind="critical"` styling (consistent with "Leave room")
  - The "Report room" button must **NOT** have any additional className (do not add `mx_RoomSummaryCard_report` or similar)
  - Use the i18n key `action|report_room` for the button label

#### ReportRoomDialog Component Structure
- Create a new `ReportRoomDialog` component (`src/components/views/dialogs/ReportRoomDialog.tsx`)
- Export as named export: `export const ReportRoomDialog`
- Component interface: `{ roomId: string; onFinished(complete: boolean): void }`

#### Dialog Container (BaseDialog)
- Use `BaseDialog` as the dialog wrapper with these props:
  - `className="mx_ReportRoomDialog"`
  - `title={_t("report_room|title")}`
  - `contentId="mx_ReportEventDialog"`
  - `onFinished` should call `onFinished(sent)` where `sent` is the submission state

#### Form Structure (using @vector-im/compound-web)
- Use `Root` component from compound-web as the form container with:
  - `id="mx_ReportEventDialog"`
  - `onSubmit` handler for form submission
- Inside `Root`, the content order must be:
  1. Description paragraph: `<p>{_t("report_room|description")}</p>`
  2. Admin message (if configured)
  3. Field component with textarea
  4. InlineSpinner (if busy)
  5. DialogButtons
- Use `Field` component with `name="reason"` containing:
  - `Label` with `htmlFor="mx_ReportRoomDialog_reason"` and text from i18n key `room_settings|permissions|ban_reason`
  - `textarea` element with:
    - `id="mx_ReportRoomDialog_reason"`
    - `placeholder={_t("report_room|reason_placeholder")}`
    - `rows={5}`
  - `ErrorMessage` component for displaying errors (render conditionally: `{error ? <ErrorMessage>{error}</ErrorMessage> : null}`)
- Use `InlineSpinner` for loading state (render conditionally: `{busy ? <InlineSpinner /> : null}`)
- Use `DialogButtons` component (from `../elements/DialogButtons`) for action buttons with:
  - `primaryButton={_t("action|send_report")}`
  - `onPrimaryButtonClick` for submit handler
  - `focus={true}`
  - `onCancel` handler
  - `disabled={busy}`

#### State Management
- When form is not yet submitted (`!sent`): show the form inside `Root`
- When form is submitted (`sent`): show only confirmation message `{_t("report_room|sent")}` as a `<p>` element

#### Admin Message Configuration
- Get admin message via `SdkConfig.getObject("report_event")?.get("admin_message_md", "adminMessageMD")` (note: two parameters required)
- If configured, render as HTML via `new Markdown(adminMessageMD).toHTML({ externalLinks: true })`
- Display in a `<p>` element with `dangerouslySetInnerHTML`

**Acceptance**:
- When clicking the "Report room" menu item in the room summary card, a modal dialog opens
- When a user enters a reason and clicks submit, the report is sent via `MatrixClient.reportRoom(roomId, reason)`
- When the report is successfully submitted, a confirmation message is displayed
- When an admin message is configured, it is rendered as HTML via Markdown conversion
- The room summary card renders with the new "Report room" option visible

## Verification Strategy

Trace each visible action to the state transition or room target it controls: cover pending reset and forced-verification gates, precise notification getters, pinned-identity dismissal copy, reply recipients, and report-dialog validation and submission. Existing identity and room behavior must remain unchanged when the new states or controls do not apply.

# Environment Dependency Changes (relative to Base Env)

No additional milestone-specific packages are required beyond the retained entry image and its existing Element Web base environment.
