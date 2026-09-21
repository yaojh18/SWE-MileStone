# Software Requirements Specification: Complete Mutiny Reactive Streaming Support

## Overview

Provide an end-to-end Mutiny integration for Dubbo Triple streaming. A backpressure-aware publisher/subscriber layer must bridge Java Flow and Dubbo stream observers on both client and server. Call utilities then expose all four RPC shapes through Uni and Multi, method handlers connect generated stubs to service implementations, and the compiler provides the Mutiny generator entry point for those stubs. Cancellation, terminal signals, error conversion, lifecycle hooks, and generator selection must remain consistent across the complete stack.

### Requirements Summary

1. **FR1**: Mutiny Publisher Contract Across Client and Server Streams
2. **FR2**: Mutiny Subscriber Contract Across Client and Server Streams
3. **FR3**: Mutiny Client Call Utilities
4. **FR4**: Mutiny Server Call Utilities
5. **FR5**: Mutiny Method Handlers
6. **FR6**: Mutiny Code Generator

### Affected Modules

- dubbo-plugin/dubbo-mutiny publisher/subscriber abstractions
- dubbo-plugin/dubbo-mutiny client/server call utilities and method handlers
- dubbo-plugin/dubbo-compiler Mutiny generator entry point

## Functional Requirements

### Functional Area: Reactive stream foundation

Publisher and subscriber adapters provide the lifecycle, signal, and backpressure contract shared by every RPC shape.

### FR1: Mutiny Publisher Contract Across Client and Server Streams
The shared publisher lifecycle and its client/server adapters form one backpressure-aware bridge from Dubbo observers to downstream Mutiny consumers.

**Problem**:
There is no abstraction layer to bridge Dubbo's `CallStreamObserver` with the Java Flow Publisher API, preventing reactive stream consumption using Mutiny.

Client applications using Mutiny cannot consume streaming responses from Triple protocol servers as reactive streams.

Server implementations using Mutiny cannot consume streaming requests from clients as reactive streams.

**Requirements**:
- Provide an abstract base class that implements both `Flow.Publisher<T>` and `Flow.Subscription` interfaces
- Extend `CancelableStreamObserver<T>` to integrate with Dubbo's existing stream observer infrastructure
- Support a single subscription model where only one subscriber can subscribe at a time
- Implement backpressure handling by accumulating requests before the subscription is ready (i.e., before `onSubscribe` binds the underlying stream observer), then forwarding accumulated requests via `CallStreamObserver.request(n)` once the subscription is established
- Disable automatic flow control on the underlying subscription to enable manual backpressure management
- Forward data items from `CallStreamObserver` callbacks to the downstream `Flow.Subscriber`
- Propagate error and completion signals to the downstream subscriber
- Support optional shutdown hooks that execute exactly once on cancel, error, or completion
- Support optional subscribe callbacks to notify when the underlying stream observer is bound
- Track cancellation and completion states to prevent duplicate signal propagation

- Extend the abstract publisher base class for client-side usage
- Override the `beforeStart` callback to bind the client call observer adapter as the subscription source
- Support construction with optional subscribe callback and shutdown hook for lifecycle management
- Enable streaming patterns: One-to-Many, Many-to-One, and Many-to-Many from the client perspective

- Extend the abstract publisher base class for server-side usage
- Accept a `CallStreamObserver` in the constructor and immediately bind it as the subscription source
- Enable streaming patterns: Many-to-One and Many-to-Many from the server perspective

**Acceptance**:
- When a `Flow.Subscriber` subscribes to the publisher, it receives `onSubscribe` with the publisher as the subscription
- When `request(n)` is called before the stream is ready, requests are accumulated
- When the stream becomes ready, accumulated requests are forwarded to the underlying observer
- When `onNext` is called on the stream observer, the item is forwarded to the downstream subscriber
- When `onError` or `onCompleted` is called, the corresponding signal is forwarded and the shutdown hook executes
- When `cancel` is called, the shutdown hook executes
- When attempting to subscribe a second subscriber, only the first subscription succeeds (subsequent subscribe attempts are silently ignored, following the existing Reactor implementation pattern)

- When the client call starts, the publisher binds to the `ClientCallToObserverAdapter`
- When streaming responses arrive, they are published to the Mutiny subscriber
- When the server completes the stream, the completion signal propagates to the subscriber
- When the server sends an error, the error signal propagates to the subscriber

- When constructed with a `CallStreamObserver`, the publisher is immediately ready to publish
- When streaming requests arrive from the client, they are published to the Mutiny subscriber
- When the client completes the request stream, the completion signal propagates to the subscriber
- When the client sends an error, the error signal propagates to the subscriber

### FR2: Mutiny Subscriber Contract Across Client and Server Streams
The shared subscriber lifecycle and its client/server adapters form one bridge from Mutiny producers to Dubbo request and response observers.

**Problem**:
There is no abstraction layer to bridge the Java Flow Subscriber API with Dubbo's `CallStreamObserver`, preventing reactive stream production using Mutiny.

Client applications using Mutiny cannot send streaming requests to Triple protocol servers as reactive streams.

Server implementations using Mutiny cannot send streaming responses to clients as reactive streams.

**Requirements**:
- Provide an abstract base class that implements `Flow.Subscriber<T>` interface
- Support binding to a downstream `CallStreamObserver<T>` for data forwarding
- Implement a single subscription model that cancels duplicate upstream subscriptions
- Forward data items from the upstream `Flow.Publisher` to the downstream `CallStreamObserver`
- Request one additional item from upstream after each successful `onNext` delivery via `subscription.request(1)` to implement item-by-item flow control (reactive streams backpressure pattern)
- Propagate error and completion signals to the downstream observer
- Support cancellation that propagates to the upstream subscription
- Track cancellation and completion states to prevent operations after termination

- Extend the abstract subscriber base class for client-side usage
- Override the `cancel` method to also cancel the underlying client call by invoking `ClientCallToObserverAdapter.cancel(Exception)` with a cancellation exception
- Enable the client to cancel the entire RPC call when the reactive stream is cancelled

- Extend the abstract subscriber base class for server-side usage
- Collect all items received from the upstream publisher for result aggregation
- Provide a `CompletableFuture` that completes with the collected items on stream completion or exceptionally on error
- When subscribing to a `CancelableStreamObserver` downstream, register a cancellation listener to propagate cancellation
- Manage cancellation context by creating one if not present on the downstream observer

**Acceptance**:
- When `subscribe(CallStreamObserver)` is called, the downstream is bound
- When `onSubscribe` is called with a subscription, subsequent duplicate subscriptions are cancelled
- When `onNext` is called with an item, it is forwarded to the downstream and one more item is requested
- When `onError` is called, the error is forwarded to the downstream observer
- When `onComplete` is called, the completion signal is forwarded to the downstream observer
- When `cancel` is called, the upstream subscription is cancelled

- When items are published upstream, they are forwarded to the server via the client call observer
- When `cancel` is called, both the upstream subscription and the client call are cancelled
- When the upstream completes, the client call is half-closed signaling end of request stream

- When items are published upstream, they are both forwarded to the client and collected internally
- When `getExecutionFuture` is called, it returns a future representing the stream completion
- When the upstream completes normally, the future completes with the list of collected items
- When the upstream errors, the future completes exceptionally with the error
- When the downstream cancellation context fires, the upstream subscription is cancelled

### Functional Area: RPC integration and generated APIs

Client and server utilities, method handlers, and generated stubs must carry that reactive contract across the complete Triple service API.

### FR3: Mutiny Client Call Utilities
**Problem**: The Mutiny module lacks client-side utilities to convert RPC calls into Mutiny reactive types (Uni and Multi), preventing developers from using Mutiny's reactive programming model on the client side.

**Requirements**:
- Provide a utility class for converting client-side Triple protocol calls to Mutiny reactive types
- Support unary-to-unary calls: accept a `Uni<TRequest>` and return a `Uni<TResponse>`
- Support unary-to-stream calls: accept a `Uni<TRequest>` and return a `Multi<TResponse>` that emits multiple response items
- Support stream-to-unary calls: accept a `Multi<TRequest>` and return a `Uni<TResponse>`
- Support bidirectional streaming calls: accept a `Multi<TRequest>` and return a `Multi<TResponse>`
- Properly propagate errors from the underlying RPC calls to the Mutiny publishers
- Integrate with the existing `StubInvocationUtil` for making actual RPC calls
- Use `ClientTripleMutinyPublisher` and `ClientTripleMutinySubscriber` for stream handling

**Acceptance**:
- Follow the naming and structure pattern established by `dubbo-reactive` module (e.g., `ReactorClientCalls` → corresponding Mutiny utility class)
- The utility class must use the utility class pattern (private constructor, static methods)
- Provide static methods for each of the four gRPC streaming patterns, using Mutiny types:
  - Unary: accepts `Uni<TRequest>`, returns `Uni<TResponse>`
  - Server-streaming: accepts `Uni<TRequest>`, returns `Multi<TResponse>`
  - Client-streaming: accepts `Multi<TRequest>`, returns `Uni<TResponse>`
  - Bidirectional: accepts `Multi<TRequest>`, returns `Multi<TResponse>`
- When a unary client call succeeds, the returned Uni emits the response value
- When a unary client call fails, the returned Uni propagates the error through Mutiny's failure mechanism
- When a server-streaming call is made, the returned Multi emits all items sent by the server
- When a client-streaming call is made, all items from the request Multi are sent to the server


### FR4: Mutiny Server Call Utilities
**Problem**: The Mutiny module lacks server-side utilities to adapt incoming RPC requests into Mutiny reactive types and convert Mutiny responses back to stream observers, preventing developers from implementing services using Mutiny's reactive programming model.

**Requirements**:
- Provide a utility class for converting server-side stream observers to Mutiny reactive types
- Support unary-to-unary calls: accept a request and `StreamObserver<R>`, apply a `Function<Uni<T>, Uni<R>>`, and send the result to the observer
- Support unary-to-stream calls: accept a request and `StreamObserver<R>`, apply a `Function<Uni<T>, Multi<R>>`, and send all emitted items to the observer
- Support stream-to-unary calls: return a `StreamObserver<T>` that collects requests into a Multi, apply a `Function<Multi<T>, Uni<R>>`, and send the result to the response observer
- Support bidirectional streaming calls: return a `StreamObserver<T>` for requests, apply a `Function<Multi<T>, Multi<R>>`, and send all response items to the response observer
- Handle null responses by failing with a NOT_FOUND status
- Convert failures on unary and server-streaming response paths through `TriRpcStatus`; client-streaming and bidirectional paths propagate the original throwable to the response observer while respecting cancellation
- Handle cancellation scenarios appropriately
- Use `ServerTripleMutinyPublisher` and `ServerTripleMutinySubscriber` for stream handling

**Acceptance**:
- Follow the naming and structure pattern established by `dubbo-reactive` module (e.g., `ReactorServerCalls` → corresponding Mutiny utility class)
- The utility class must use the utility class pattern (private constructor, static methods)
- Provide static methods for each of the four gRPC streaming patterns:
  - Unary: accepts request, `StreamObserver<R>`, and a `Function<Uni<T>, Uni<R>>`
  - Server-streaming: accepts request, `StreamObserver<R>`, and a `Function<Uni<T>, Multi<R>>`; returns a `CompletableFuture` for tracking completion
  - Client-streaming: accepts `StreamObserver<R>` and a `Function<Multi<T>, Uni<R>>`; returns a `StreamObserver<T>` for receiving client items
  - Bidirectional: accepts `StreamObserver<R>` and a `Function<Multi<T>, Multi<R>>`; returns a `StreamObserver<T>`
- When the service function returns a successful response, the response observer receives onNext followed by onCompleted
- Unary and server-streaming failures reach `onError` as `TriRpcStatus` exceptions; client-streaming and bidirectional failures reach `onError` as the original throwable, and cancelled observers receive no further signals
- When handling streaming responses, all emitted items are forwarded to the response observer
- When handling streaming requests, incoming items are properly collected and passed to the service function as a Multi
- When the service function returns null for unary responses, propagate a NOT_FOUND status error to the response observer


### FR5: Mutiny Method Handlers
**Problem**: Server stubs require method handlers that can invoke service implementations using Mutiny reactive types, but no such handlers exist for the Mutiny module.

**Requirements**:
- Implement `OneToOneMethodHandler` for unary-to-unary method invocations
  - Accept a `Function<Uni<T>, Uni<R>>` as the service implementation
  - Return a `CompletableFuture<R>` from the invoke method
  - Extract the request from arguments and wrap in a Uni
  - Use an observer adaptor to bridge between the Uni result and the CompletableFuture
- Implement `OneToManyMethodHandler` for unary-to-stream method invocations
  - Accept a `Function<Uni<T>, Multi<R>>` as the service implementation
  - Return a `CompletableFuture` from the invoke method
  - Extract request and response observer from arguments
  - Delegate to MutinyServerCalls.oneToMany
- Implement `ManyToOneMethodHandler` for stream-to-unary method invocations
  - Accept a `Function<Multi<T>, Uni<R>>` as the service implementation
  - Return a `CompletableFuture<StreamObserver<T>>` from the invoke method
  - Extract response observer from arguments
  - Delegate to MutinyServerCalls.manyToOne
- Implement `ManyToManyMethodHandler` for bidirectional streaming method invocations
  - Accept a `Function<Multi<T>, Multi<R>>` as the service implementation
  - Return a `CompletableFuture<StreamObserver<T>>` from the invoke method
  - Extract response observer from arguments
  - Delegate to MutinyServerCalls.manyToMany
- All handlers must implement the `StubMethodHandler<T, R>` interface

**Acceptance**:
- Follow the naming, structure, and package pattern established by `dubbo-reactive/handler/` module
- Each handler must implement the `StubMethodHandler` interface
- Each handler accepts a service function in its constructor (using Mutiny types Uni/Multi instead of Reactor types Mono/Flux)
- The `invoke(Object[] arguments)` method extracts request/observer from arguments following the same convention as the Reactor handlers
- Handler behavior for each pattern:
  - Unary handler: extracts request from arguments, applies the function, returns `CompletableFuture` with the response
  - Server-streaming handler: extracts request and response observer, delegates to the server calls utility
  - Client-streaming handler: extracts response observer, returns `CompletableFuture<StreamObserver<T>>` for receiving client items
  - Bidirectional handler: extracts response observer, returns `CompletableFuture<StreamObserver<T>>` for bidirectional communication
- When an error occurs in any handler, it is properly propagated to the response observer


### FR6: Mutiny Code Generator
**Problem**: Developers cannot automatically generate Mutiny-based stub code from Protocol Buffer service definitions, requiring manual implementation of Mutiny service interfaces.

**Requirements**:
- Implement `MutinyDubbo3TripleGenerator` that extends `AbstractGenerator`
- Use "Dubbo" as the class name prefix
- Use "Triple" as the class name suffix
- Use "MutinyDubbo3TripleStub.mustache" as the template file name for stub generation
- Use "MutinyDubbo3TripleInterfaceStub.mustache" as the template file name for interface generation
- Always use multiple template mode (interface + implementation separate)
- Do not support single template mode (throw exception if requested)
- Provide a main method entry point that invokes `DubboGeneratorPlugin.generate`

**Acceptance**:
- Follow the naming and structure pattern established by `ReactorDubbo3TripleGenerator` in `dubbo-compiler` module
- The generator must extend `AbstractGenerator` and override the template configuration methods
- Use the same class prefix/suffix pattern as other Triple generators ("Dubbo" prefix, "Triple" suffix)
- Select the existing `MutinyDubbo3TripleInterfaceStub.mustache` and `MutinyDubbo3TripleStub.mustache` templates; this task does not add or modify template files
- The generator must use multiple template mode (separate interface and implementation files)
- Provide a main method entry point following the pattern of other generators
- When the generator processes a proto file with service definitions, it produces Mutiny-based stub code using Uni/Multi types

## Verification Strategy

Use the Mutiny publisher, subscriber, client-call, server-call, and handler tests as one functional suite covering unary, server-streaming, client-streaming, and bidirectional calls. Verify that the compiler discovers the Mutiny generator, selects its interface and implementation templates, and emits separate Mutiny stub types.

# Environment Dependency Changes (relative to Base Env)

No environment dependency changes beyond the repository state at the milestone start.
