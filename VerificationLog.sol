// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title VerificationLog
/// @notice Stores tamper-evident face-verification records on-chain.
contract VerificationLog {
    struct Record {
        string faceHash;
        string matchedUrl;
        uint256 timestamp;
        string challengeNonce;
    }

    Record[] private _records;

    event RecordAdded(string faceHash, string matchedUrl, uint256 timestamp);

    /// @notice Append a new verification record.
    /// @param faceHash        SHA-256 hex digest of the *live* face encoding.
    /// @param matchedUrl      Social-media URL found via reverse image search.
    /// @param timestamp       Unix timestamp (seconds) when the match was recorded.
    /// @param challengeNonce  One-time liveness challenge bound to this session.
    function addRecord(
        string calldata faceHash,
        string calldata matchedUrl,
        uint256 timestamp,
        string calldata challengeNonce
    ) external {
        _records.push(Record(faceHash, matchedUrl, timestamp, challengeNonce));
        emit RecordAdded(faceHash, matchedUrl, timestamp);
    }

    /// @notice Total number of stored records.
    function recordCount() external view returns (uint256) {
        return _records.length;
    }

    /// @notice Fetch a record by index (0-based).
    function getRecord(uint256 index)
        external
        view
        returns (
            string memory faceHash,
            string memory matchedUrl,
            uint256 timestamp,
            string memory challengeNonce
        )
    {
        require(index < _records.length, "Index out of bounds");
        Record storage r = _records[index];
        return (r.faceHash, r.matchedUrl, r.timestamp, r.challengeNonce);
    }
}
