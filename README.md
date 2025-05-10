# Trade Pattern Detection System

*This project was developed as a technical test for FundingPips.*

## Overview

This project implements a high-performance system for detecting and categorizing similar trading patterns between different trading accounts. The system analyzes historical trading data to identify copy trades, reversal trades, and partial copy trades between accounts, with special attention to performance optimization for large datasets (1M+ trades).

## Problem Statement

The system addresses the need to detect potential trading pattern similarities between accounts, which could indicate:
- Copy trading (same direction trades)
- Reversal trading (opposite direction trades)
- Partial copy trading (similar trades with slightly different lot sizes)

The system can operate in two modes:
- **Mode A**: Copying is allowed between accounts owned by the same user
- **Mode B**: Copying is NOT allowed between accounts of the same user (violations are reported)

## Technical Implementation

### Data Structure

The system processes binary trade data with the following structure:
```
- Open date (milliseconds timestamp)
- Close date (milliseconds timestamp)
- Duration (integer, in milliseconds)
- Lot size (integer, L * 10^8)
- Side (enum: 1 = buy, 0 = sell)
- Trade ID (integer)
- Symbol (enum integer)
- Account ID (integer)
- User ID (integer)
```

This integer-based data structure was chosen for several key performance reasons:

1. **Memory Efficiency**: Integers require significantly less memory than strings or floating-point numbers. For a dataset with millions of trades, this results in substantial memory savings.

2. **Processing Speed**: Integer comparisons and operations are much faster than string comparisons or floating-point operations. This is critical for the high-performance requirements of processing large datasets.

3. **Binary Storage**: Using integers allows for efficient binary storage with fixed-size records using the `struct` module, enabling:
   - Direct memory mapping
   - Faster I/O operations
   - Chunk-based processing
   - Predictable memory usage

4. **Symbol Enumeration**: Instead of storing repeated symbol strings (like "EURUSD"), symbols are mapped to integer IDs, dramatically reducing storage requirements and comparison costs.

5. **Precision Control**: For decimal values like lot sizes, scaling by a factor (10^8) and storing as integers prevents floating-point precision errors that could affect calculations.

6. **Timestamp Efficiency**: Using millisecond timestamps as integers simplifies time window calculations and comparisons.

This design prioritizes performance and scalability, which is essential when processing millions of trades while maintaining reasonable memory usage and execution time.

### Key Components

#### 1. Configuration and Data Loading

The system uses Pydantic for configuration validation, ensuring that input parameters are correctly specified. It loads:
- Dataset path
- Symbols mapping
- Operating mode (A or B)

#### 2. Trade Filtering**

Initial filtering is applied to all trades:
- Only trades from specified user IDs are processed
- Trades with duration ≤ 1 second AND lot size < 0.01 are filtered out

#### 3. Batch Processing System

The core of the implementation uses a linked-list based batch processing system:

- `TradesBatch`: Abstract base class that maintains:
  - A parent trade
  - A linked list of next batches
  - A collection of similar trades

The system uses specialized class implementations for each trade pattern type:

- **Specialized Implementations**:
  - `CopyTradesBatch`: Detects trades in the same direction
  - `ReversalTradesBatch`: Detects trades in the opposite direction
  - `PartialCopyTradesBatch`: Detects trades with similar lot sizes (within 30%)

This class hierarchy design provides several key advantages:

1. **Reduced Conditional Logic Overhead**: 
   - Each class implements only the specific validation logic needed for its pattern type
   - Eliminates the need for complex if-else chains or switch statements that would be required in a monolithic approach
   - Python method dispatch is more efficient than repeated runtime condition checking

2. **Memory Efficiency**:
   - Each implementation carries only the data structures needed for its specific pattern detection
   - The abstract base class defines `__slots__` to minimize memory footprint
   - Specialized classes inherit this memory optimization while adding only necessary functionality

3. **Polymorphic Processing**:
   - The `categorize_trade` function can process any batch type through polymorphism
   - This allows for a single processing algorithm to handle all pattern types
   - Reduces code duplication while maintaining separation of concerns

4. **Optimized Validation Logic**:
   - `CopyTradesBatch` only checks for matching trade directions
   - `ReversalTradesBatch` only checks for opposite trade directions
   - `PartialCopyTradesBatch` focuses on lot size comparison logic
   - Each implementation avoids unnecessary calculations irrelevant to its pattern type

5. **Extensibility**:
   - New pattern types can be added by creating new subclasses
   - Existing pattern detection logic remains untouched when adding new patterns
   - Core algorithm remains stable while allowing for feature expansion

The abstract base class provides common functionality like time window validation and linked list management, while each subclass implements only the specific validation logic needed for its pattern type. This design minimizes the computational overhead for each trade comparison while maintaining a clean, maintainable code structure.

#### 4. Trade Categorization Algorithm

The `categorize_trade` function is the heart of the system, which implements a specialized linked list approach:

##### Symbol-Based Partitioning

The system maintains separate linked lists for each trading symbol (e.g., EURUSD, GBPJPY), which provides several advantages:
- **Reduced Search Space**: Trades can only match if they have the same symbol, so partitioning by symbol immediately eliminates ~99% of potential comparisons
- **Reduced Memory Access Overhead**: Since all trades in a particular linked list share the same symbol, the algorithm avoids redundant symbol comparison operations during batch processing
- **Parallelization Potential**: Symbol-based partitioning could enable future parallel processing

Compared to a single global list approach, this partitioning reduces the algorithmic complexity from O(n) to O(n/s) per trade, where s is the number of symbols.

##### Batch Concept and Linked List Structure

Each "batch" in the system serves two purposes:
1. **Data Container**: Stores a parent trade and its matching similar trades
2. **Linked List Node**: Acts as a node in a time-ordered linked list

The linked list structure was chosen over alternatives (like arrays or hash maps) for several key reasons:
- **Efficient Time Window Management**: As trades expire (move outside the 5-minute window), they can be removed from the front of the list in O(1) time without shifting elements
- **Dynamic Growth**: The structure grows organically as new trades arrive, without requiring resizing operations
- **Memory Efficiency**: Only relevant trades remain in memory, as expired trades are automatically removed

Compared to an array-based approach, which would require O(n) operations to remove expired elements, the linked list provides O(1) removal at the cost of slightly less efficient traversal.

##### Algorithm Flow

For each incoming trade, the system:
1. Takes a new trade and attempts to match it with existing batches in its symbol's linked list
2. Validates trades based on:
   - Time window (±5 minutes)
   - Account ownership
   - Symbol matching
   - Trade direction (depending on the batch type)
   - Lot size similarity (for partial copies)
3. If a match is found, adds the trade to the matching batch
4. If no match is found, creates a new batch with this trade as the parent
5. Automatically prunes expired batches from the front of the list
6. Returns the updated linked list structure

The implementation stores frequently accessed values in local variables to reduce Python's attribute lookup overhead:
```python
# Extract values once to local variables to reduce lookup overhead
open_ts = trade[OPEN_DATE_INDEX]
close_ts = trade[CLOSE_DATE_INDEX]
value_to_validate = trade[index]
account_id = trade[ACCOUNT_ID_INDEX]
```

This optimization is particularly important in Python, where each attribute access or dictionary lookup has a performance cost. By extracting these values once at the beginning of the function, the code avoids repeated lookups during the batch processing loop.

This approach ensures that each trade is processed efficiently while maintaining the time-ordered relationship between trades.

#### 5. Memory Optimization

Several techniques are used to optimize memory usage:
- `__slots__` in the `TradesBatch` class to reduce memory overhead
- Binary data reading with `struct` for efficient parsing
- Chunked reading (1000 trades at a time) to reduce I/O overhead and context switching
- Symbol enumeration to avoid storing repeated string values
- Early filtering of trades that don't match user IDs or minimum criteria

The chunk-based reading approach (`while chunk := file.read(block_size * 1000)`) provides significant performance benefits:
- Reduces context switching overhead between kernel space and user space
- Minimizes disk I/O latency by reading larger blocks at once instead of individual records
- Balances memory usage with I/O efficiency
- Allows for efficient batch processing of trades

## Performance Considerations

1. **Time Complexity**:
   - The algorithm processes each trade once, giving O(n) complexity for n trades
   - For each trade, it checks against existing batches, but the linked list structure and symbol-based partitioning limit the number of comparisons

2. **Memory Efficiency**:
   - The system uses a streaming approach, processing trades sequentially
   - Only relevant trades and their relationships are stored in memory
   - Binary data reading reduces memory overhead

3. **Optimizations**:
   - Symbol-based partitioning to reduce the search space
   - Two-stage filtering of irrelevant trades
   - Use of `__slots__` to reduce memory footprint
   - Chunked reading to balance memory usage and I/O performance
   - Linked list structure for efficient time-window management

## Setup and Installation

### Requirements

The system requires Python 3.13+ and the following dependencies:
- pydantic (for configuration validation)

To set up the environment:

```
# Create and activate a virtual environment (optional but recommended)
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### Directory Structure

Ensure you have the following directory structure for storing results:
```
results/
```

You can create it with:
```
mkdir -p results
```

## Usage

### Data Preparation

Before running the system, the trading data must be converted to a binary format for efficient processing:

1. **Using transform_dataset.py**: Convert CSV trading data to binary format
   ```
   python transform_dataset.py output_dataset.bin accounts_info.csv trade_data1.csv trade_data2.csv ...
   ```
   This script:
   - Takes CSV trading data files as input
   - Converts timestamps, lot sizes, and other fields to their binary representation
   - Creates a symbols mapping file (symbols.json)
   - Outputs a binary file ready for high-performance processing

2. **Using generate_dataset.py**: Create synthetic data for testing
   ```
   python generate_dataset.py [seed] [hours]
   ```
   This script:
   - Generates a synthetic dataset with random trades
   - Uses the provided seed for reproducibility
   - Creates trades spanning the specified number of hours
   - Useful for performance testing with large datasets

### Running the Analysis

The script takes the following command-line arguments:
1. Configuration file path
2. First user ID to analyze
3. Second user ID to analyze

The configuration file should specify:
- `dataset_path`: Path to the binary dataset
- `symbols_path`: Path to the symbols mapping file
- `mode`: "A" or "B" depending on whether copying between same-user accounts is allowed

Example:
```
python solution.py config.json 42 57
```

## Output

The system generates three CSV reports:
1. Copy trades
2. Reversal trades
3. Partial copy trades

In Mode B, the reports also indicate violations (trades between accounts owned by the same user).

## Design Decisions and Evolution

The implementation evolved through careful consideration of performance requirements and Python's specific characteristics. It incorporates principles from data-oriented programming to optimize for modern CPU architectures and memory access patterns. Here's how the design decisions were made and why they're effective:

### 1. Data Structure Selection

**Linked List Structure**: After evaluating several approaches (arrays, hash maps, trees), a linked list was chosen for the time-window management:

- **Why it's better**: Unlike arrays which require O(n) time to remove elements from the beginning, linked lists allow O(1) removal of expired trades. This is critical when processing millions of trades in a sliding time window.
- **Alternative considered**: A priority queue based on timestamps was considered but would have added unnecessary complexity since trades are already processed in chronological order.
- **Implementation detail**: The linked list is implemented through the `next_batch` reference in each `TradesBatch` object, creating a lightweight structure without the overhead of a dedicated container class.

### 2. Symbol-Based Partitioning

**Separate Linked Lists per Symbol**: The system maintains independent linked lists for each trading symbol:

- **Why it's better**: This immediately reduces the search space by a factor equal to the number of symbols (typically 30-100 in forex trading). A trade on EURUSD will never match with a trade on GBPJPY, so there's no need to compare them.
- **Alternative considered**: A single global list with filtering would require checking every trade against the symbol condition, adding unnecessary comparisons.
- **Implementation detail**: Using dictionary lookups with integer symbol IDs as keys provides O(1) access to the correct linked list, further optimizing the process.

### 3. Object-Oriented Design with Polymorphism

**Abstract Base Class with Specialized Implementations**:

- **Why it's better**: By using polymorphism through the abstract `TradesBatch` class and specialized implementations, the code avoids complex conditional logic that would be needed in a monolithic approach.
- **Alternative considered**: A single class with conditional logic would be more difficult to maintain and extend, and would perform unnecessary checks for each trade type.
- **Implementation detail**: Each subclass implements only the specific validation logic it needs, reducing the computational overhead for each comparison.

### 4. Memory Optimization Techniques

Several techniques were implemented to minimize memory usage:

- **`__slots__`**: Reduces per-instance memory overhead by eliminating the instance `__dict__`.
- **Binary data processing**: Uses `struct` for efficient binary data handling instead of text parsing.
- **Chunked reading**: Processes data in chunks of 1000 trades to balance memory usage with I/O efficiency.
- **Local variable caching**: Stores frequently accessed values in local variables to reduce attribute lookup overhead.
- **Early filtering**: Quickly eliminates irrelevant trades before more expensive processing.

### 5. Python-Specific and Data-Oriented Optimizations

The implementation addresses Python's specific performance characteristics and incorporates data-oriented programming principles:

- **Attribute lookup reduction**: Python's attribute lookups are relatively expensive, so frequently accessed values are cached in local variables.
- **Primitive types**: Uses integers instead of strings or floats where possible to reduce object overhead.
- **Minimized object creation**: Reuses existing objects rather than creating new ones when possible.
- **Reduced function call overhead**: Implements critical paths with minimal function call depth.
- **Data-oriented approach**: Organizes data for efficient access patterns rather than focusing solely on object relationships:
  - Uses tuple-based trade representation instead of class instances
  - Separates data by symbol to improve locality of reference
  - Structures data to minimize cache misses
  - Processes data in batches to maximize CPU efficiency

### 6. Two-Stage Filtering Approach

The implementation uses a two-stage filtering process:

- **First stage**: Quickly filters out trades that don't match user IDs or minimum criteria.
- **Second stage**: Applies more detailed matching logic only to potentially relevant trades.
- **Why it's better**: This "fail fast" approach avoids expensive processing for trades that can be quickly determined to be irrelevant.

### 7. Minimal External Dependencies

The implementation relies almost exclusively on Python's standard library:

- **Why it's better**: Reduces complexity, eliminates dependency management issues, and ensures better control over performance characteristics.
- **Exception**: Pydantic is used only for configuration validation, where its benefits in ensuring correct input parameters outweigh the cost of the dependency.

### 8. Balance of Simplicity and Performance

The design prioritizes a balance between code simplicity and performance:

- **Clean, maintainable code**: The implementation is structured for readability and maintainability.
- **Performance-critical optimizations**: Optimizations are focused on the most performance-critical parts of the code.
- **Explicit over implicit**: The code favors explicit, clear approaches over clever but obscure optimizations.

This approach resulted in a solution that efficiently processes millions of trades while maintaining a clean, maintainable codebase that can be extended or modified as requirements evolve.

## Conclusion

This implementation provides a high-performance solution for detecting trading patterns across large datasets. The design prioritizes:
- Memory efficiency
- Processing speed
- Scalability
- Accuracy in pattern detection
- Simplicity and maintainability

The system successfully meets all the requirements while maintaining good performance characteristics for large datasets. The implementation strikes a balance between algorithmic efficiency and code simplicity, focusing on a clean, maintainable solution that performs well without unnecessary complexity or external dependencies.
